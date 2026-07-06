"""AI test run routes — submit, stream, cancel, result, credential profiles, environments."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_roles
from app.core.logging import get_logger
from app.models.ai_runs import (
    AICredentialProfile,
    AIRunEvent,
    AIRunStatus,
    AISkill,
    AITestRun,
)
from app.models.user import User, UserRole
from app.schemas.ai_runs import (
    AIRunCreate,
    AIRunEventResponse,
    AIRunListItem,
    AIRunListResponse,
    AIRunResponse,
    AISkillListResponse,
    AISkillResponse,
    CredentialProfileCreate,
    CredentialProfileResponse,
    SkillReplayRequest,
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
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Create a new credential profile with optional encrypted credentials."""
    try:
        credentials_json = None
        if payload.credentials:
            from app.services.credential_service import encrypt_credentials
            credentials_json = encrypt_credentials(payload.credentials)

        profile = AICredentialProfile(
            name=payload.name,
            project_id=payload.project_id,
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
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
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
        require_roles(UserRole.admin, UserRole.qa_lead, UserRole.qa_engineer)
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

        environment = payload.environment
        if not environment and payload.project_id:
            from app.models.project import Project
            proj = db.get(Project, payload.project_id)
            if proj:
                environment = proj.name

        run = AITestRun(
            goal=payload.goal,
            environment=environment,
            project_id=payload.project_id,
            credential_profile_id=payload.credential_profile_id,
            credential_profile_name=profile_name,
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
    """Return paginated list of AI test runs."""
    try:
        offset = (page - 1) * limit
        total = int(
            db.execute(text("SELECT COUNT(*) FROM ai_test_runs")).scalar() or 0
        )
        rows = db.execute(
            text(
                "SELECT id, goal, environment, credential_profile_name, status,"
                "  started_at, completed_at, duration_ms, step_count, run_type,"
                "  created_at"
                " FROM ai_test_runs"
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
                created_at=r.created_at,
            )
            for r in rows
        ]
        return AIRunListResponse(data=items, total=total, page=page, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("List runs DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/runs/{run_id}", response_model=AIRunResponse)
def get_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single AI test run with all its events."""
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="AI test run not found")

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
            raise HTTPException(status_code=404, detail="AI test run not found")

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


# ── Skills ───────────────────────────────────────────────────────────────────

@router.get("/skills", response_model=AISkillListResponse)
def list_skills(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paginated list of saved skills (replayable action recordings)."""
    try:
        total = db.query(AISkill).count()
        skills = (
            db.query(AISkill)
            .order_by(AISkill.updated_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return AISkillListResponse(
            data=[AISkillResponse.model_validate(s) for s in skills],
            total=total,
            page=page,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        logger.error("List skills DB error: %s", exc)
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
    return AISkillResponse.model_validate(skill)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_200_OK)
def delete_skill(
    skill_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
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
        require_roles(UserRole.admin, UserRole.qa_lead, UserRole.qa_engineer)
    ),
):
    """Replay a saved skill's recorded actions without LLM planning.

    Creates a normal AI test run (run_type="skill_replay") so the frontend
    can reuse the exact same live-stream and result views as goal runs."""
    try:
        skill = db.get(AISkill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if not skill.history_json:
            raise HTTPException(
                status_code=409, detail="Skill has no recorded actions to replay"
            )

        profile_id = payload.credential_profile_id or skill.credential_profile_id
        profile_name = None
        if profile_id:
            profile = db.get(AICredentialProfile, profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=404, detail="Credential profile not found"
                )
            profile_name = profile.name

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
