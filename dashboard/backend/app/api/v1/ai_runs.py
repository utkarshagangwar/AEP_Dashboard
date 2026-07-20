"""AI test run routes — submit, stream, cancel, result, credential profiles, environments."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
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
from app.models.visual_qa import VisualFinding, VisualRun
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
    CredentialProfileCreate,
    CredentialProfileResponse,
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

@router.post("/runs", status_code=status.HTTP_201_CREATED)
def submit_run(
    payload: AIRunCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission("vibe_testing")
    ),
):
    """Submit a new AI test goal. Returns run_id immediately; execution is async."""
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

        run = AITestRun(
            goal=payload.goal,
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
        db.add(run)
        db.commit()
        db.refresh(run)

        from app.workers.tasks.ai_execution import run_ai_test_task
        run_ai_test_task.delay(str(run.id))

        write_audit_log(
            db,
            user_id=current_user.id,
            action="submit_ai_run",
            resource_type="ai_test_run",
            resource_id=str(run.id),
            details={"goal_preview": payload.goal[:200]},
            ip_address=_client_ip(request),
        )

        logger.info("AI run %s submitted by %s", run.id, current_user.id)
        return {"run_id": str(run.id), "status": "pending"}

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
    (the New Autonomous Visual QA Run flow) so both appear in one history."""
    try:
        offset = (page - 1) * limit
        total = int(
            db.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM ai_test_runs)"
                    " + (SELECT COUNT(*) FROM orchestrator_runs)"
                )
            ).scalar()
            or 0
        )
        rows = db.execute(
            text(
                "SELECT id, goal, environment, credential_profile_name, status,"
                "  started_at, completed_at, duration_ms, step_count, run_type,"
                "  platform, created_at"
                " FROM ("
                "   SELECT id, goal, environment, credential_profile_name,"
                "     status::text AS status, started_at, completed_at,"
                "     duration_ms, step_count, run_type, platform, created_at"
                "   FROM ai_test_runs"
                "   UNION ALL"
                "   SELECT r.id,"
                "     COALESCE(r.goal, 'Visual audit (no goal specified)') AS goal,"
                "     r.environment, cp.name AS credential_profile_name,"
                "     r.status::text AS status, r.started_at, r.completed_at,"
                "     r.duration_ms,"
                "     (SELECT COUNT(*) FROM orchestrator_step_decisions d"
                "        WHERE d.run_id = r.id AND d.invoked = true) AS step_count,"
                "     'autonomous_qa' AS run_type, 'web' AS platform, r.created_at"
                "   FROM orchestrator_runs r"
                "   LEFT JOIN ai_credential_profiles cp ON cp.id = r.credential_profile_id"
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
                created_at=r.created_at,
            )
            for r in rows
        ]
        return AIRunListResponse(data=items, total=total, page=page, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("List runs DB error: %s", exc)
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
            return _cancel_orchestrator_run(run_id, db, current_user, request)

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
    TERMINAL = frozenset({"passed", "failed", "inconclusive", "cancelled"})

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
