"""AI test run routes — submit, stream, cancel, result, credential profiles, environments."""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.ai_runs import (
    AICredentialProfile,
    AIRunEvent,
    AIRunStatus,
    AISkill,
    AITestRun,
)
from app.models.orchestrator import OrchestratorRun, OrchestratorRunStatus, OrchestratorStepDecision
from app.models.visual_qa import VisualFinding, VisualRun, VisualRunStatus
from app.models.user import User, UserRole
from app.schemas.ai_runs import (
    AIRunCreate,
    AIRunEventResponse,
    AIRunListItem,
    AIRunListResponse,
    AIRunResponse,
    AISkillListResponse,
    AISkillResponse,
    AISkillUpdate,
    BulkAssignProjectRequest,
    BulkSkillIds,
    CoverageRequirementGroup,
    CoverageResponse,
    CoverageTestEntry,
    CredentialProfileCreate,
    CredentialProfileResponse,
    FunctionalTestDataSet,
    OrchestratorDecisionResponse,
    SkillReplayRequest,
    VisualFindingResponse,
)
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)
router = APIRouter(prefix="/ai-testing", tags=["ai-testing-runs"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Credential Profiles ──────────────────────────────────────────────────────

@router.get("/credential-profiles", response_model=list[CredentialProfileResponse])
def list_credential_profiles(
    project_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all credential profiles, optionally scoped to a project."""
    try:
        q = db.query(AICredentialProfile)
        if project_id:
            q = q.filter(AICredentialProfile.project_id == project_id)
        return q.order_by(AICredentialProfile.name).all()
    except SQLAlchemyError as exc:
        logger.error("List credential profiles error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post(
    "/credential-profiles",
    response_model=CredentialProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_credential_profile(
    payload: CredentialProfileCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Create a new credential profile with optional encrypted credentials.

    kind="bypass" profiles store an admin API-key login secret capable of
    impersonating any user on the target app, so they require the admin
    role on top of the vibe_testing permission every other profile needs —
    checked here (not via a second route-level Depends) since it depends on
    the parsed request body, and stacking a role-Depends on the route would
    require admin for every profile, including plain ones.
    """
    if payload.kind == "bypass" and current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creating a bypass credential profile requires an admin role",
        )
    try:
        credentials_json = None
        if payload.credentials:
            from app.services.credential_service import encrypt_credentials
            credentials_json = encrypt_credentials(payload.credentials)

        profile = AICredentialProfile(
            name=payload.name,
            project_id=payload.project_id,
            kind=payload.kind,
            target_url=payload.target_url,
            allowed_domains=payload.allowed_domains,
            credentials_json=credentials_json,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="create_credential_profile",
            resource_type="ai_credential_profile",
            resource_id=str(profile.id),
            ip_address=_client_ip(request),
        )
        return profile
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Create credential profile error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.delete("/credential-profiles/{profile_id}", status_code=status.HTTP_200_OK)
def delete_credential_profile(
    profile_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    try:
        profile = db.get(AICredentialProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Credential profile not found")
        db.delete(profile)
        db.commit()
        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_credential_profile",
            resource_type="ai_credential_profile",
            resource_id=str(profile_id),
            ip_address=_client_ip(request),
        )
        return {"message": "Credential profile deleted"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ── Environments ─────────────────────────────────────────────────────────────

@router.get("/environments")
def list_environments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return active projects as selectable test environments."""
    try:
        rows = db.execute(
            text("SELECT id, name FROM projects WHERE is_active = true ORDER BY name")
        ).fetchall()
        return [{"id": str(r.id), "name": r.name} for r in rows]
    except SQLAlchemyError as exc:
        logger.error("List environments error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ── AI Test Runs ─────────────────────────────────────────────────────────────

def _compile_functional_goal(
    payload: AIRunCreate, data_set: FunctionalTestDataSet | None = None
) -> str:
    """Turn a structured Functional Test (preconditions/steps/expected
    results/test_type/one data set) into the plain-language goal text the
    Hands browser agent already knows how to consume — no change needed to
    ai_runner.py, ai_eval.py, or Skill auto-save, all of which only ever
    read AITestRun.goal.
    """
    lines: list[str] = []
    if payload.test_type and payload.test_type != "happy":
        label = "NEGATIVE" if payload.test_type == "negative" else "EDGE CASE"
        lines.append(
            f"Test type: {label} — deliberately exercise invalid input, "
            "boundary values, or an error path per the steps below, and "
            "confirm the application handles it the way Expected Results "
            "describes (not the happy path)."
        )
        lines.append("")
    if payload.preconditions and payload.preconditions.strip():
        lines.append("Preconditions (assume true before Step 1):")
        lines.append(payload.preconditions.strip())
        lines.append("")
    lines.append("Steps:")
    for i, step in enumerate(payload.steps or [], start=1):
        lines.append(f"{i}. {step.text.strip()}")
    lines.append("")
    if data_set is not None and data_set.values:
        lines.append(f'Test data for this run ("{data_set.name}"):')
        for k, v in data_set.values.items():
            lines.append(f"- {k}: {v}")
        lines.append("(Use these exact values wherever a step references input data.)")
        lines.append("")
    lines.append("Expected results — the test only passes if ALL of these hold:")
    for r in payload.expected_results or []:
        if r.strip():
            lines.append(f"- {r.strip()}")
    return "\n".join(lines).strip()


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def submit_run(
    payload: AIRunCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission("vibe_testing")
    ),
):
    """Submit a new AI test goal, or a structured Functional Test.

    A structured Functional Test with N test_data sets creates N
    ai_test_runs rows (data-driven execution) instead of one — the caller
    gets back the first run's id (for the existing single-run live view)
    plus the full run_ids list.
    """
    try:
        profile_name = None
        if payload.credential_profile_id:
            profile = db.get(AICredentialProfile, payload.credential_profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Credential profile not found",
                )
            profile_name = profile.name
            # Defense-in-depth alongside AIRunCreate's schema validator,
            # which can't reach the DB to check kind: bypass injects a
            # Playwright browser cookie and has no Android counterpart yet.
            if payload.platform == "android" and (profile.kind or "standard") == "bypass":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Bypass credential profiles are not supported for Android runs",
                )

        android_app_build_name = None
        if payload.platform == "android":
            from app.models.ai_runs import AndroidAppBuild

            build = db.get(AndroidAppBuild, payload.android_app_build_id)
            if build is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Android app build not found",
                )
            android_app_build_name = build.name

        environment = payload.environment
        if not environment and payload.project_id:
            from app.models.project import Project
            proj = db.get(Project, payload.project_id)
            if proj:
                environment = proj.name

        # One-off "Website without/with login" path — mutually exclusive
        # with credential_profile_id (enforced by AIRunCreate's validator).
        # Never persisted as a reusable profile; the password is still
        # encrypted at rest here even though it's one-off.
        adhoc_credentials_json = None
        if payload.login_identifier and payload.login_password:
            from app.services.credential_service import encrypt_credentials
            adhoc_credentials_json = encrypt_credentials(
                {"username": payload.login_identifier, "password": payload.login_password}
            )

        common_kwargs = dict(
            environment=environment,
            project_id=payload.project_id,
            credential_profile_id=payload.credential_profile_id,
            credential_profile_name=profile_name,
            adhoc_target_url=payload.target_url,
            adhoc_credentials_json=adhoc_credentials_json,
            platform=payload.platform,
            android_app_build_id=payload.android_app_build_id,
            android_app_build_name=android_app_build_name,
            device_profile=payload.device_profile,
            status=AIRunStatus.pending,
            created_by=current_user.id,
        )

        from app.workers.tasks.ai_execution import run_ai_test_task

        runs: list[AITestRun] = []
        if payload.test_category == "functional":
            data_sets = payload.test_data or [None]
            structured_kwargs = dict(
                test_category="functional",
                preconditions=payload.preconditions,
                steps=[s.model_dump() for s in (payload.steps or [])],
                expected_results=payload.expected_results,
                test_data=[d.model_dump() for d in (payload.test_data or [])] or None,
                test_type=payload.test_type,
                linked_requirement=payload.linked_requirement,
                viewport_preset=payload.viewport_preset,
            )
            for data_set in data_sets:
                compiled_goal = _compile_functional_goal(payload, data_set)
                run = AITestRun(goal=compiled_goal, **structured_kwargs, **common_kwargs)
                db.add(run)
                runs.append(run)
        else:
            run = AITestRun(goal=payload.goal, **common_kwargs)
            db.add(run)
            runs.append(run)

        db.commit()
        for run in runs:
            db.refresh(run)
            run_ai_test_task.delay(str(run.id))

        write_audit_log(
            db,
            user_id=current_user.id,
            action="submit_ai_run",
            resource_type="ai_test_run",
            resource_id=str(runs[0].id),
            details={
                "goal_preview": runs[0].goal[:200],
                "run_count": len(runs),
            },
            ip_address=_client_ip(request),
        )

        logger.info(
            "AI run(s) %s submitted by %s", [str(r.id) for r in runs], current_user.id
        )
        return {
            "run_id": str(runs[0].id),
            "status": "pending",
            "run_ids": [str(r.id) for r in runs],
        }

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Submit run DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/runs", response_model=AIRunListResponse)
def list_runs(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paginated list of AI test runs — plain "ai"/"skill_replay" runs
    from ai_test_runs, merged with "autonomous_qa" runs from orchestrator_runs
    (the New Autonomous Visual QA Run flow) and "ui_test" runs from
    visual_runs (the UI Test flow, New Vibe Test Phase 5, E.22) so all three
    appear in one unified history — a UI Test is now tracked the same way a
    Functional Test is, not left only reachable through the standalone
    GET /api/v1/visual-audits list with no browsable history in this tab.
    A visual_runs row has no `goal` column at all (it's a comparison, not an
    instruction) — synthesized here as "UI Test: {target_url}" purely for
    display in this shared list; the detail view (frontend) fetches the real
    VisualRun record directly from /api/v1/visual-audits/{id} rather than
    forcing it through AIRunResponse's shape, since findings/images have no
    equivalent there. step_count here is repurposed as "number of findings"
    for a ui_test row — same "reuse the STEPS column for whatever count is
    meaningful to this run type" convention orchestrator_runs rows already
    use (their step_count is "invoked routing decisions", not agent steps)."""
    try:
        offset = (page - 1) * limit
        total = int(
            db.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM ai_test_runs)"
                    " + (SELECT COUNT(*) FROM orchestrator_runs)"
                    " + (SELECT COUNT(*) FROM visual_runs)"
                )
            ).scalar()
            or 0
        )
        rows = db.execute(
            text(
                "SELECT id, goal, environment, credential_profile_name, status,"
                "  started_at, completed_at, duration_ms, step_count, run_type,"
                "  platform, test_category, test_type, linked_requirement, created_at"
                " FROM ("
                "   SELECT id, goal, environment, credential_profile_name,"
                "     status::text AS status, started_at, completed_at,"
                "     duration_ms, step_count, run_type, platform,"
                "     test_category, test_type, linked_requirement, created_at"
                "   FROM ai_test_runs"
                "   UNION ALL"
                "   SELECT r.id,"
                "     COALESCE(r.goal, 'Visual audit (no goal specified)') AS goal,"
                "     r.environment, cp.name AS credential_profile_name,"
                "     r.status::text AS status, r.started_at, r.completed_at,"
                "     r.duration_ms,"
                "     (SELECT COUNT(*) FROM orchestrator_step_decisions d"
                "        WHERE d.run_id = r.id AND d.invoked = true) AS step_count,"
                "     'autonomous_qa' AS run_type, 'web' AS platform,"
                "     NULL AS test_category, NULL AS test_type,"
                "     NULL AS linked_requirement, r.created_at"
                "   FROM orchestrator_runs r"
                "   LEFT JOIN ai_credential_profiles cp ON cp.id = r.credential_profile_id"
                "   UNION ALL"
                "   SELECT vr.id,"
                "     ('UI Test: ' || vr.target_url) AS goal,"
                "     vr.environment, NULL AS credential_profile_name,"
                "     vr.status::text AS status, vr.started_at, vr.completed_at,"
                "     vr.duration_ms,"
                "     (SELECT COUNT(*) FROM visual_findings vf"
                "        WHERE vf.run_id = vr.id) AS step_count,"
                "     'ui_test' AS run_type, 'web' AS platform,"
                "     'ui' AS test_category, NULL AS test_type,"
                "     vr.linked_requirement, vr.created_at"
                "   FROM visual_runs vr"
                " ) combined_runs"
                " ORDER BY created_at DESC"
                " LIMIT :lim OFFSET :off"
            ),
            {"lim": limit, "off": offset},
        ).fetchall()

        items = [
            AIRunListItem(
                id=r.id,
                goal=r.goal,
                environment=r.environment,
                credential_profile_name=r.credential_profile_name,
                status=r.status.value if hasattr(r.status, "value") else r.status,
                started_at=r.started_at,
                completed_at=r.completed_at,
                duration_ms=r.duration_ms,
                step_count=r.step_count or 0,
                run_type=r.run_type or "ai",
                platform=r.platform or "web",
                test_category=r.test_category,
                test_type=r.test_type,
                linked_requirement=r.linked_requirement,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return AIRunListResponse(data=items, total=total, page=page, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("List runs DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


def _compute_flakiness_rate(decided_statuses: list) -> Optional[float]:
    """Fraction of consecutive pairs in a chronological, decided-only
    ("passed"/"failed") status sequence that differ from their predecessor
    (New Vibe Test Phase 7, F.26). 0.0 for an all-same sequence (however
    long), approaching 1.0 for a sequence that alternates every run. None
    if there are fewer than 2 decided runs to compare."""
    if len(decided_statuses) < 2:
        return None
    transitions = sum(
        1
        for prev, cur in zip(decided_statuses, decided_statuses[1:])
        if prev != cur
    )
    return round(transitions / (len(decided_statuses) - 1), 3)


def _coverage_functional_entry(runs: list) -> CoverageTestEntry:
    """Build one functional-test coverage row from every AITestRun sharing
    the same (linked_requirement, goal_hash) group, oldest-first (see
    get_coverage). The group's own identity isn't part of the entry itself
    — the caller nests this under its CoverageRequirementGroup."""
    latest = runs[-1]
    pass_count = sum(1 for r in runs if r.status == AIRunStatus.passed)
    fail_count = sum(1 for r in runs if r.status == AIRunStatus.failed)
    needs_review_count = sum(1 for r in runs if r.status == AIRunStatus.needs_review)
    decided = pass_count + fail_count  # needs_review/inconclusive/cancelled/pending/running excluded
    # runs is already oldest-first (see get_coverage's order_by), so this
    # preserves chronological order for the flakiness calculation.
    decided_statuses = [
        "passed" if r.status == AIRunStatus.passed else "failed"
        for r in runs
        if r.status in (AIRunStatus.passed, AIRunStatus.failed)
    ]
    label = " ".join((latest.goal or "").split())
    if len(label) > 140:
        label = label[:137] + "..."
    return CoverageTestEntry(
        kind="functional",
        label=label,
        test_type=latest.test_type,
        latest_run_id=latest.id,
        latest_status=latest.status.value if hasattr(latest.status, "value") else latest.status,
        last_run_at=latest.created_at,
        latest_eval_score=latest.eval_score,
        latest_pixel_mismatch_pct=None,
        total_runs=len(runs),
        pass_count=pass_count,
        fail_count=fail_count,
        needs_review_count=needs_review_count,
        pass_rate=round(pass_count / decided, 3) if decided else None,
        flakiness_rate=_compute_flakiness_rate(decided_statuses),
    )


def _coverage_visual_entry(runs: list) -> CoverageTestEntry:
    """Same as _coverage_functional_entry, for a group of VisualRun rows
    sharing (linked_requirement, target_url, artifact_id)."""
    latest = runs[-1]
    latest_status = latest.status.value if hasattr(latest.status, "value") else latest.status
    pass_count = sum(
        1 for r in runs
        if (r.status.value if hasattr(r.status, "value") else r.status) == "passed"
    )
    fail_count = sum(
        1 for r in runs
        if (r.status.value if hasattr(r.status, "value") else r.status) in ("failed", "error")
    )
    decided = pass_count + fail_count
    # runs is oldest-first (see get_coverage); "error" folds into "failed"
    # here too so a flip between the two isn't counted as a spurious
    # transition — both mean "this run did not pass".
    decided_statuses = []
    for r in runs:
        s = r.status.value if hasattr(r.status, "value") else r.status
        if s == "passed":
            decided_statuses.append("passed")
        elif s in ("failed", "error"):
            decided_statuses.append("failed")
    return CoverageTestEntry(
        kind="ui",
        label=latest.target_url,
        test_type=None,
        latest_run_id=latest.id,
        latest_status=latest_status,
        last_run_at=latest.created_at,
        latest_eval_score=None,
        latest_pixel_mismatch_pct=latest.pixel_mismatch_pct,
        total_runs=len(runs),
        pass_count=pass_count,
        fail_count=fail_count,
        needs_review_count=0,
        pass_rate=round(pass_count / decided, 3) if decided else None,
        flakiness_rate=_compute_flakiness_rate(decided_statuses),
    )


@router.get("/coverage", response_model=CoverageResponse)
def get_coverage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Requirement -> linked test(s) -> latest status -> (functional) GEval
    score -> last-run date (New Vibe Test Phase 6, A.4/D.15) — "is this
    feature actually fully tested, visually and functionally" answerable at
    a glance, and per-test pass-rate so a genuinely broken feature can be
    told apart from a flaky test.

    Grouping key (no schema change — computed here, not stored; see
    schemas.ai_runs's CoverageRequirementGroup/CoverageTestEntry docstring
    for the full rationale): a functional test is
    (linked_requirement, goal_hash); a UI test is
    (linked_requirement, target_url, artifact_id). Every run in a group
    contributes to that one test's pass_count/fail_count/pass_rate; the
    group's most recent run (by created_at) is what "latest status" means.
    """
    try:
        from app.services.skill_store import compute_goal_hash

        func_rows = (
            db.query(AITestRun)
            .filter(
                AITestRun.test_category == "functional",
                AITestRun.linked_requirement.isnot(None),
                AITestRun.linked_requirement != "",
            )
            .order_by(AITestRun.created_at.asc())
            .all()
        )
        func_groups: dict[tuple, list] = {}
        for r in func_rows:
            key = (r.linked_requirement, compute_goal_hash(r.goal or ""))
            func_groups.setdefault(key, []).append(r)

        visual_rows = (
            db.query(VisualRun)
            .filter(VisualRun.linked_requirement.isnot(None), VisualRun.linked_requirement != "")
            .order_by(VisualRun.created_at.asc())
            .all()
        )
        visual_groups: dict[tuple, list] = {}
        for r in visual_rows:
            key = (r.linked_requirement, r.target_url, str(r.artifact_id))
            visual_groups.setdefault(key, []).append(r)

        by_requirement: dict[str, CoverageRequirementGroup] = {}
        for (req, _hash), runs in func_groups.items():
            group = by_requirement.setdefault(req, CoverageRequirementGroup(linked_requirement=req))
            group.functional_tests.append(_coverage_functional_entry(runs))
        for (req, _url, _artifact), runs in visual_groups.items():
            group = by_requirement.setdefault(req, CoverageRequirementGroup(linked_requirement=req))
            group.ui_tests.append(_coverage_visual_entry(runs))

        for group in by_requirement.values():
            group.functional_tests.sort(key=lambda e: e.last_run_at, reverse=True)
            group.ui_tests.sort(key=lambda e: e.last_run_at, reverse=True)

        unlinked_functional_count = (
            db.query(AITestRun)
            .filter(
                AITestRun.test_category == "functional",
                (AITestRun.linked_requirement.is_(None)) | (AITestRun.linked_requirement == ""),
            )
            .count()
        )
        unlinked_ui_count = (
            db.query(VisualRun)
            .filter(
                (VisualRun.linked_requirement.is_(None)) | (VisualRun.linked_requirement == "")
            )
            .count()
        )

        return CoverageResponse(
            requirements=sorted(by_requirement.values(), key=lambda g: g.linked_requirement),
            unlinked_functional_count=unlinked_functional_count,
            unlinked_ui_count=unlinked_ui_count,
        )
    except SQLAlchemyError as exc:
        logger.error("Coverage report DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


def _get_orchestrator_run(run_id: UUID, db: Session) -> AIRunResponse:
    """Build an AIRunResponse for an autonomous QA (orchestrator) run.

    Shaped differently from a plain AITestRun: no step events, instead a
    routing decision trail (which sub-agents ran) plus, if Judge ran,
    the linked visual run's findings — pulled in directly so the Results
    tab can render a full report without a second round-trip.
    """
    run = db.get(OrchestratorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    credential_profile_name = None
    if run.credential_profile_id is not None:
        profile = db.get(AICredentialProfile, run.credential_profile_id)
        credential_profile_name = profile.name if profile else None

    decisions = (
        db.query(OrchestratorStepDecision)
        .filter(OrchestratorStepDecision.run_id == run_id)
        .order_by(OrchestratorStepDecision.sequence)
        .all()
    )

    pixel_mismatch_pct = None
    findings: list[VisualFindingResponse] = []
    if run.visual_run_id is not None:
        visual_run = db.get(VisualRun, run.visual_run_id)
        if visual_run is not None:
            pixel_mismatch_pct = visual_run.pixel_mismatch_pct
            findings = [
                VisualFindingResponse(
                    engine=f.engine.value if hasattr(f.engine, "value") else f.engine,
                    severity=f.severity.value if hasattr(f.severity, "value") else f.severity,
                    element=f.element,
                    issue=f.issue,
                    expected=f.expected,
                    actual=f.actual,
                )
                for f in db.query(VisualFinding)
                .filter(VisualFinding.run_id == run.visual_run_id)
                .all()
            ]

    return AIRunResponse(
        id=run.id,
        goal=run.goal or "Visual audit (no goal specified)",
        environment=run.environment,
        project_id=run.project_id,
        credential_profile_id=run.credential_profile_id,
        credential_profile_name=credential_profile_name,
        status=run.status.value if hasattr(run.status, "value") else run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        step_count=sum(1 for d in decisions if d.invoked),
        summary=run.summary,
        run_type="autonomous_qa",
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
        events=[],
        error_message=run.error_message,
        ai_test_run_id=run.ai_test_run_id,
        visual_run_id=run.visual_run_id,
        self_execute_answer=run.self_execute_answer,
        pixel_mismatch_pct=pixel_mismatch_pct,
        decisions=[
            OrchestratorDecisionResponse(
                step=d.step.value if hasattr(d.step, "value") else d.step,
                invoked=d.invoked,
                model_provider=d.model_provider,
                model_name=d.model_name,
                is_deterministic=d.is_deterministic,
                rationale=d.rationale,
                sequence=d.sequence,
            )
            for d in decisions
        ],
        findings=findings,
    )


@router.get("/runs/{run_id}", response_model=AIRunResponse)
def get_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single run with all its events — either a plain AI test run
    or an autonomous QA (orchestrator) run, whichever table has this id."""
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            return _get_orchestrator_run(run_id, db)

        events = (
            db.query(AIRunEvent)
            .filter(AIRunEvent.run_id == run_id)
            .order_by(AIRunEvent.sequence)
            .all()
        )
        return AIRunResponse(
            id=run.id,
            goal=run.goal,
            environment=run.environment,
            project_id=run.project_id,
            credential_profile_id=run.credential_profile_id,
            credential_profile_name=run.credential_profile_name,
            status=run.status.value if hasattr(run.status, "value") else run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_ms=run.duration_ms,
            step_count=run.step_count or 0,
            summary=run.summary,
            raw_summary=run.raw_summary,
            run_type=run.run_type or "ai",
            skill_id=run.skill_id,
            failing_step_index=run.failing_step_index,
            failing_step_description=run.failing_step_description,
            failing_step_screenshot_url=run.failing_step_screenshot_url,
            video_available=bool(run.video_path and os.path.isfile(run.video_path)),
            eval_score=run.eval_score,
            eval_reason=run.eval_reason,
            eval_status=run.eval_status,
            visual_eval_score=run.visual_eval_score,
            visual_eval_reason=run.visual_eval_reason,
            visual_eval_status=run.visual_eval_status,
            test_category=run.test_category,
            preconditions=run.preconditions,
            steps=run.steps,
            expected_results=run.expected_results,
            test_data=run.test_data,
            test_type=run.test_type,
            linked_requirement=run.linked_requirement,
            viewport_preset=run.viewport_preset,
            created_by=run.created_by,
            created_at=run.created_at,
            updated_at=run.updated_at,
            platform=run.platform or "web",
            android_app_build_id=run.android_app_build_id,
            android_app_build_name=run.android_app_build_name,
            device_profile=run.device_profile,
            platform_metadata=run.platform_metadata,
            events=[
                AIRunEventResponse(
                    id=e.id,
                    run_id=e.run_id,
                    sequence=e.sequence,
                    status=e.status.value if hasattr(e.status, "value") else e.status,
                    description=e.description,
                    step_type=(
                        e.step_type.value if hasattr(e.step_type, "value") else e.step_type
                    ),
                    elapsed_ms=e.elapsed_ms,
                    screenshot_url=e.screenshot_url,
                    highlighted_element=e.highlighted_element,
                    is_failing_step=e.is_failing_step or False,
                    created_at=e.created_at,
                )
                for e in events
            ],
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Get run DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.delete("/runs/{run_id}", status_code=status.HTTP_200_OK)
def cancel_run(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a pending/running AI test run, or delete a finished one.

    Same verb, two behaviors: an in-flight run is cancelled (kept in
    history); a run that already reached a terminal status is permanently
    deleted along with its events (FK cascade)."""
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            orchestrator_run = db.get(OrchestratorRun, run_id)
            if orchestrator_run is not None:
                return _cancel_orchestrator_run(run_id, db, current_user, request)
            # Phase 5 (E.22): a UI Test run (visual_runs) now appears in this
            # same unified list/history — its delete button hits this same
            # endpoint, so it needs its own fallback, same pattern as
            # orchestrator runs above.
            return _cancel_visual_run(run_id, db, current_user, request)

        if run.status in (AIRunStatus.pending, AIRunStatus.running):
            run.status = AIRunStatus.cancelled
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            write_audit_log(
                db,
                user_id=current_user.id,
                action="cancel_ai_run",
                resource_type="ai_test_run",
                resource_id=str(run_id),
                ip_address=_client_ip(request),
            )
            return {"message": "Run cancelled"}

        # Free the recording (if any) before the row goes away — nothing
        # else references it once history is deleted. Best-effort: a
        # missing/already-gone file must never block deleting the run row.
        if run.video_path:
            try:
                if os.path.isfile(run.video_path):
                    os.remove(run.video_path)
            except OSError:
                logger.warning("Failed to remove video file for run %s", run_id, exc_info=True)

        db.delete(run)
        db.commit()
        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_ai_run",
            resource_type="ai_test_run",
            resource_id=str(run_id),
            ip_address=_client_ip(request),
        )
        return {"message": "Run deleted"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error") from exc


def _cancel_orchestrator_run(
    run_id: UUID, db: Session, current_user: User, request: Request
) -> dict:
    """Same cancel-or-delete behavior as cancel_run, for orchestrator runs.

    Only the orchestrator_runs row (and its step decisions, via FK cascade)
    is removed — the linked AITestRun/VisualRun sub-runs are left intact
    since they're independently viewable history."""
    run = db.get(OrchestratorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    in_flight = run.status in (
        OrchestratorRunStatus.pending,
        OrchestratorRunStatus.planning,
        OrchestratorRunStatus.running,
    )
    if in_flight:
        run.status = OrchestratorRunStatus.cancelled
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        write_audit_log(
            db,
            user_id=current_user.id,
            action="cancel_ai_run",
            resource_type="orchestrator_run",
            resource_id=str(run_id),
            ip_address=_client_ip(request),
        )
        return {"message": "Run cancelled"}

    db.delete(run)
    db.commit()
    write_audit_log(
        db,
        user_id=current_user.id,
        action="delete_ai_run",
        resource_type="orchestrator_run",
        resource_id=str(run_id),
        ip_address=_client_ip(request),
    )
    return {"message": "Run deleted"}


def _cancel_visual_run(
    run_id: UUID, db: Session, current_user: User, request: Request
) -> dict:
    """Same cancel-or-delete behavior as cancel_run, for a UI Test run
    (visual_runs) — Phase 5 (E.22): now reachable from the same unified
    Results list/delete button as Functional Test runs, not just the
    standalone Visual Audit surface. VisualRun has no "running" concept of
    its own that a client can meaningfully interrupt mid-flight the way an
    AITestRun/OrchestratorRun task can (the visual_audit Celery task is
    short — pixel-diff + one vision call — and doesn't poll for a cancel
    flag) — so, matching visual_audit.py's own cancel_run endpoint, only a
    still-pending run can be marked cancelled; anything else (including
    "running") is deleted outright, same as a terminal AITestRun/
    OrchestratorRun above."""
    run = db.get(VisualRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status == VisualRunStatus.pending:
        run.status = VisualRunStatus.cancelled
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        write_audit_log(
            db,
            user_id=current_user.id,
            action="cancel_ai_run",
            resource_type="visual_run",
            resource_id=str(run_id),
            ip_address=_client_ip(request),
        )
        return {"message": "Run cancelled"}

    # Free the screenshot/diff images (if any) before the row goes away —
    # same best-effort convention as cancel_run's video_path cleanup above;
    # a missing/already-gone file must never block deleting the run row.
    # The reference design image itself is NOT removed — it's a reusable
    # Memory Bank artifact (design_artifacts), independent of any one run.
    for path in (run.screenshot_path, run.diff_image_path):
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.warning("Failed to remove image file for visual run %s", run_id, exc_info=True)

    db.delete(run)  # findings cascade via VisualFinding's FK (ondelete=CASCADE)
    db.commit()
    write_audit_log(
        db,
        user_id=current_user.id,
        action="delete_ai_run",
        resource_type="visual_run",
        resource_id=str(run_id),
        ip_address=_client_ip(request),
    )
    return {"message": "Run deleted"}


# ── Skills ───────────────────────────────────────────────────────────────────

def _project_names(db: Session, project_ids: set) -> dict:
    """id -> name for a set of project ids, one query regardless of set size."""
    project_ids = {pid for pid in project_ids if pid is not None}
    if not project_ids:
        return {}
    from app.models.project import Project

    rows = db.query(Project.id, Project.name).filter(Project.id.in_(project_ids)).all()
    return {pid: name for pid, name in rows}


def _skill_response(skill: AISkill, project_names: dict) -> AISkillResponse:
    resp = AISkillResponse.model_validate(skill)
    resp.project_name = project_names.get(skill.project_id)
    return resp


# Name sorts case-insensitively (func.lower) so "apple" and "Banana" don't
# sort purely by ASCII case; id is a secondary key so paginated ordering is
# stable even when many rows share a sort value (e.g. identical timestamps).
_SKILL_SORT_COLUMNS = {
    "name": func.lower(AISkill.name),
    "created_at": AISkill.created_at,
    "updated_at": AISkill.updated_at,
}


@router.get("/skills", response_model=AISkillListResponse)
def list_skills(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    # UUID string to scope to one project, "none" to scope to unassigned
    # skills, or omitted for every project — the multi-project categorization
    # that keeps a skill for Project A from being confused with Project B's.
    project_id: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paginated list of saved skills, optionally scoped to a project."""
    try:
        if sort_by not in _SKILL_SORT_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"sort_by must be one of: {', '.join(_SKILL_SORT_COLUMNS)}",
            )
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")

        q = db.query(AISkill)
        if project_id == "none":
            q = q.filter(AISkill.project_id.is_(None))
        elif project_id:
            try:
                q = q.filter(AISkill.project_id == UUID(project_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="project_id must be a UUID or 'none'")

        total = q.count()
        column = _SKILL_SORT_COLUMNS[sort_by]
        order = column.asc() if sort_dir == "asc" else column.desc()
        skills = (
            q.order_by(order, AISkill.id)
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        names = _project_names(db, {s.project_id for s in skills})
        return AISkillListResponse(
            data=[_skill_response(s, names) for s in skills],
            total=total,
            page=page,
            limit=limit,
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("List skills DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/skills/bulk-delete", status_code=status.HTTP_200_OK)
def bulk_delete_skills(
    payload: BulkSkillIds,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Delete every skill in skill_ids that exists, in one transaction.
    IDs that don't match any row are silently ignored (already gone is not
    an error for a bulk operation) — the response count tells the caller
    how many rows were actually removed."""
    try:
        skills = db.query(AISkill).filter(AISkill.id.in_(payload.skill_ids)).all()
        deleted_ids = [str(s.id) for s in skills]
        for skill in skills:
            db.delete(skill)
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="bulk_delete_ai_skills",
            resource_type="ai_skill",
            details={"skill_ids": deleted_ids, "count": len(deleted_ids)},
            ip_address=_client_ip(request),
        )
        logger.info("Bulk-deleted %d skill(s) by %s", len(deleted_ids), current_user.id)
        return {"deleted": len(deleted_ids)}
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Bulk delete skills DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/skills/bulk-assign-project", status_code=status.HTTP_200_OK)
def bulk_assign_project(
    payload: BulkAssignProjectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Reassign every skill in skill_ids to project_id (or unassign, if
    project_id is null) in one transaction. Marks each as manually_edited,
    same as the single-skill PATCH path, so a later SOW/video re-analysis of
    the source checkpoint won't silently move it back."""
    try:
        skills = db.query(AISkill).filter(AISkill.id.in_(payload.skill_ids)).all()
        updated_ids = [str(s.id) for s in skills]
        for skill in skills:
            skill.project_id = payload.project_id
            skill.manually_edited = True
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="bulk_assign_ai_skills_project",
            resource_type="ai_skill",
            details={
                "skill_ids": updated_ids,
                "count": len(updated_ids),
                "project_id": str(payload.project_id) if payload.project_id else None,
            },
            ip_address=_client_ip(request),
        )
        logger.info(
            "Bulk-assigned %d skill(s) to project %s by %s",
            len(updated_ids), payload.project_id, current_user.id,
        )
        return {"updated": len(updated_ids)}
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Bulk assign skills DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/skills/{skill_id}", response_model=AISkillResponse)
def get_skill(
    skill_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = db.get(AISkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_response(skill, _project_names(db, {skill.project_id}))


@router.patch("/skills/{skill_id}", response_model=AISkillResponse)
def update_skill(
    skill_id: UUID,
    payload: AISkillUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Manually view/edit a skill's name, goal text, or project assignment.

    Editing the goal on a skill that has a recorded action history clears
    that history — the recording no longer matches the edited instructions,
    so the next run re-plans with AI and records fresh actions instead of
    silently replaying steps that don't match what's now written down.
    Sets manually_edited=True so a later SOW/video re-analysis of this
    skill's source checkpoint won't overwrite the edit (see skill_store)."""
    try:
        skill = db.get(AISkill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")

        fields = payload.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="Name cannot be empty")
            skill.name = name[:300]

        goal_changed = False
        if "goal" in fields:
            goal = (fields["goal"] or "").strip()
            if not goal:
                raise HTTPException(status_code=400, detail="Goal cannot be empty")
            goal_changed = goal != skill.goal
            if goal_changed:
                from app.services.skill_store import compute_goal_hash
                skill.goal = goal
                skill.goal_hash = compute_goal_hash(goal)

        if "project_id" in fields:
            skill.project_id = fields["project_id"]

        if goal_changed and skill.history_json is not None:
            skill.history_json = None
            skill.step_count = 0

        skill.manually_edited = True
        db.commit()
        db.refresh(skill)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="update_ai_skill",
            resource_type="ai_skill",
            resource_id=str(skill_id),
            details={"fields": list(fields.keys())},
            ip_address=_client_ip(request),
        )
        logger.info("Skill %s updated by %s (fields=%s)", skill_id, current_user.id, list(fields.keys()))
        return _skill_response(skill, _project_names(db, {skill.project_id}))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Update skill DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.delete("/skills/{skill_id}", status_code=status.HTTP_200_OK)
def delete_skill(
    skill_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    try:
        skill = db.get(AISkill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        db.delete(skill)
        db.commit()
        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_ai_skill",
            resource_type="ai_skill",
            resource_id=str(skill_id),
            ip_address=_client_ip(request),
        )
        return {"message": "Skill deleted"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/skills/{skill_id}/replay", status_code=status.HTTP_201_CREATED)
def replay_skill(
    skill_id: UUID,
    payload: SkillReplayRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission("vibe_testing")
    ),
):
    """Run a saved skill.

    If it has a recorded action history, replay it deterministically (no LLM
    planning) — run_type="skill_replay". Otherwise it's a prompt-only skill
    (extracted from a SOW/video, never actually run yet): start a normal
    AI-planned run using its instruction text as the goal — run_type="ai".
    A pass there naturally upgrades this same row with a real recording via
    the existing goal-based auto-save (matched by goal_hash), no special
    casing needed. Either way this creates a normal AI test run so the
    frontend can reuse the exact same live-stream and result views."""
    try:
        skill = db.get(AISkill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")

        profile_id = payload.credential_profile_id or skill.credential_profile_id
        profile_name = None
        if profile_id:
            profile = db.get(AICredentialProfile, profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=404, detail="Credential profile not found"
                )
            profile_name = profile.name

        if skill.history_json:
            run = AITestRun(
                goal=skill.goal,
                environment=skill.environment,
                project_id=skill.project_id,
                credential_profile_id=profile_id,
                credential_profile_name=profile_name,
                status=AIRunStatus.pending,
                run_type="skill_replay",
                skill_id=skill.id,
                created_by=current_user.id,
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            from app.workers.tasks.ai_execution import replay_skill_task
            replay_skill_task.delay(
                str(run.id), str(skill.id), payload.allow_ai_fallback
            )
        else:
            run = AITestRun(
                goal=skill.goal,
                environment=skill.environment,
                project_id=skill.project_id,
                credential_profile_id=profile_id,
                credential_profile_name=profile_name,
                status=AIRunStatus.pending,
                run_type="ai",
                skill_id=skill.id,
                created_by=current_user.id,
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            from app.workers.tasks.ai_execution import run_ai_test_task
            run_ai_test_task.delay(str(run.id))

        write_audit_log(
            db,
            user_id=current_user.id,
            action="replay_ai_skill",
            resource_type="ai_skill",
            resource_id=str(skill_id),
            details={"run_id": str(run.id), "allow_ai_fallback": payload.allow_ai_fallback},
            ip_address=_client_ip(request),
        )
        logger.info("Skill %s replay submitted as run %s by %s", skill_id, run.id, current_user.id)
        return {"run_id": str(run.id), "status": "pending"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Replay skill DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ── SSE Stream (Phase 3) ─────────────────────────────────────────────────────

@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """
    Server-Sent Events stream for a single AI test run.

    Polls the DB every 500ms for new events and run status.
    Streams incremental events so the frontend log stays in sync with what
    was actually emitted live — same shape as the persisted event records,
    so Phase 5 Result view can use the same data without transformation.

    Terminates when run reaches a terminal status (passed/failed/inconclusive/cancelled).

    Note: events are upserted in place (a step is written once as "running"
    then updated to "passed"/"failed" on the same row/sequence once it
    resolves — see app/workers/tasks/ai_execution.py). A high-water mark
    or last-seen-status cache that only re-emits events it believes changed
    can drop that second write under the wrong timing (e.g. a status flip
    that a cache never observed as distinct), leaving a step stuck as
    "running" in the UI forever even after it actually finished. To avoid
    that whole class of bug, every poll simply resends the full current
    event list — the frontend already upserts by sequence, so this is
    idempotent and always reflects the true DB state.
    """
    # "needs_review" included (New Vibe Test Phase 4, D.15): _persist_result
    # can now set this instead of "passed" once GEval has weighed in, and it
    # is just as terminal as any other outcome here — a stream that didn't
    # know about it would poll/idle forever waiting for a status this run
    # will never reach.
    TERMINAL = frozenset({"passed", "failed", "inconclusive", "cancelled", "needs_review"})

    async def event_generator():
        from app.core.database import SessionLocal

        while True:
            session: Session = SessionLocal()
            try:
                run = session.get(AITestRun, run_id)
                if run is None:
                    yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                    return

                status_val = (
                    run.status.value if hasattr(run.status, "value") else run.status
                )

                new_events = (
                    session.query(AIRunEvent)
                    .filter(AIRunEvent.run_id == run_id)
                    .order_by(AIRunEvent.sequence)
                    .all()
                )

                payload = {
                    "run_status": status_val,
                    "step_count": run.step_count or 0,
                    "duration_ms": run.duration_ms,
                    "summary": run.summary,
                    "failing_step_index": run.failing_step_index,
                    "failing_step_description": run.failing_step_description,
                    "failing_step_screenshot_url": run.failing_step_screenshot_url,
                    "new_events": [
                        {
                            "sequence": e.sequence,
                            "status": (
                                e.status.value
                                if hasattr(e.status, "value")
                                else e.status
                            ),
                            "description": e.description,
                            "step_type": (
                                e.step_type.value
                                if hasattr(e.step_type, "value")
                                else e.step_type
                            ),
                            "elapsed_ms": e.elapsed_ms,
                            "screenshot_url": e.screenshot_url,
                            "highlighted_element": e.highlighted_element,
                            "is_failing_step": e.is_failing_step or False,
                        }
                        for e in new_events
                    ],
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"

                if status_val in TERMINAL:
                    return
            finally:
                session.close()

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Live browser view + recorded video (New Vibe Test / Skill Replay) ───────

@router.get("/runs/{run_id}/live-frames")
async def stream_live_frames(
    run_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Server-Sent Events relay of a run's live CDP screencast frames.

    app/services/ai_run_capture.py (running in the Celery worker container)
    publishes each frame on Redis channel ai_run_frames:{run_id} as the AI
    agent drives the browser. This endpoint just relays them to the browser
    over SSE — it never touches Chromium/CDP itself, mirroring how /stream
    above relays step events out of the DB instead of the live run.

    A run that never enabled live capture (Autonomous QA's Hands step,
    Android runs) or whose capture failed to start simply never publishes
    anything — the stream idles (same shape as /stream's "no new_events")
    until the run reaches a terminal status, at which point it closes.
    """
    import redis.asyncio as redis_asyncio

    from app.core.config import settings
    from app.models.ai_runs import AITestRun as _AITestRun

    # "needs_review" included (New Vibe Test Phase 4, D.15): _persist_result
    # can now set this instead of "passed" once GEval has weighed in, and it
    # is just as terminal as any other outcome here — a stream that didn't
    # know about it would poll/idle forever waiting for a status this run
    # will never reach.
    TERMINAL = frozenset({"passed", "failed", "inconclusive", "cancelled", "needs_review"})
    channel = f"ai_run_frames:{run_id}"

    async def event_generator():
        from app.core.database import SessionLocal

        redis_client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                session: Session = SessionLocal()
                try:
                    run = session.get(_AITestRun, run_id)
                    if run is None:
                        yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                        return
                    status_val = (
                        run.status.value if hasattr(run.status, "value") else run.status
                    )
                finally:
                    session.close()

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="ignore")
                    yield f"data: {data}\n\n"

                if status_val in TERMINAL:
                    return
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await redis_client.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/video")
def get_run_video(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Serve a run's full-session recording for inline playback or
    download. Path comes from OUR database row (server-generated, never
    client input) — no path traversal risk, same guarantee
    visual_audit.py::get_run_image relies on for its images. Range requests
    (for seeking) work for free via Starlette's FileResponse."""
    run = db.get(AITestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.video_path or not os.path.isfile(run.video_path):
        raise HTTPException(status_code=404, detail="No video available for this run")
    return FileResponse(run.video_path, media_type="video/mp4")
