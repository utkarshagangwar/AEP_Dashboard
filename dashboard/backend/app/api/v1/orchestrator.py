"""Orchestrator API ("The Brain") — submit a task, poll its routing decision
and result.

Feature-flagged: reuses VISUAL_AUDIT_ENABLED, same as visual_audit.py — every
endpoint returns 404 unless it's set, matching the fact that the
orchestrator's only entry point today (AutonomousQASection.tsx) is already
gated by it. See app/services/orchestrator.py's module docstring for why a
dedicated flag was not introduced.

Endpoints (all under /api/v1/orchestrator):
  POST   /runs                create + enqueue an orchestrated run
  GET    /runs/{run_id}       run detail incl. routing decisions
  POST   /runs/{run_id}/cancel  cancel a pending run
"""
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.orchestrator import (
    OrchestratorRun,
    OrchestratorRunStatus,
    OrchestratorStepDecision,
)
from app.models.user import User
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


def _feature_enabled() -> None:
    """Gate every endpoint behind VISUAL_AUDIT_ENABLED (default: off) —
    same flag as visual_audit.py, reused rather than duplicated (see the
    module docstring of app/services/orchestrator.py for why)."""
    if os.environ.get("VISUAL_AUDIT_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Orchestrator is not enabled")


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Schemas ──────────────────────────────────────────────────────────────────


class OrchestratorRunCreate(BaseModel):
    goal: str | None = Field(default=None, min_length=5, max_length=2000)
    target_url: str | None = Field(default=None, max_length=2000)
    artifact_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    environment: str | None = Field(default=None, max_length=200)
    credential_profile_id: uuid.UUID | None = None

    @field_validator("target_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("target_url must start with http:// or https://")
        return v


class StepDecisionOut(BaseModel):
    step: str
    invoked: bool
    model_provider: str | None
    model_name: str | None
    is_deterministic: bool
    rationale: str
    sequence: int


class OrchestratorRunOut(BaseModel):
    id: uuid.UUID
    goal: str | None
    target_url: str | None
    artifact_id: uuid.UUID | None
    environment: str | None
    status: str
    ai_test_run_id: uuid.UUID | None
    visual_run_id: uuid.UUID | None
    self_execute_answer: str | None
    summary: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: str
    decisions: list[StepDecisionOut] = []


def _run_out(
    run: OrchestratorRun, decisions: list[OrchestratorStepDecision] | None = None
) -> OrchestratorRunOut:
    return OrchestratorRunOut(
        id=run.id,
        goal=run.goal,
        target_url=run.target_url,
        artifact_id=run.artifact_id,
        environment=run.environment,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        ai_test_run_id=run.ai_test_run_id,
        visual_run_id=run.visual_run_id,
        self_execute_answer=run.self_execute_answer,
        summary=run.summary,
        error_message=run.error_message,
        duration_ms=run.duration_ms,
        created_at=run.created_at.isoformat() if run.created_at else "",
        decisions=[
            StepDecisionOut(
                step=d.step.value if hasattr(d.step, "value") else str(d.step),
                invoked=d.invoked,
                model_provider=d.model_provider,
                model_name=d.model_name,
                is_deterministic=d.is_deterministic,
                rationale=d.rationale,
                sequence=d.sequence,
            )
            for d in (decisions or [])
        ],
    )


# ── Runs ─────────────────────────────────────────────────────────────────────


@router.post("/runs", response_model=OrchestratorRunOut, status_code=202)
def create_run(
    payload: OrchestratorRunCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    if not (payload.goal or payload.target_url or payload.artifact_id):
        raise HTTPException(
            status_code=400,
            detail="Provide a goal, a design reference + URL, or both.",
        )

    try:
        run = OrchestratorRun(
            project_id=payload.project_id,
            goal=payload.goal,
            target_url=payload.target_url,
            artifact_id=payload.artifact_id,
            credential_profile_id=payload.credential_profile_id,
            environment=payload.environment,
            status=OrchestratorRunStatus.pending,
            created_by=current_user.id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Enqueue AFTER commit so the worker can always load the row
        from app.workers.tasks.orchestrator import run_orchestrator_task

        run_orchestrator_task.delay(str(run.id))

        write_audit_log(
            db,
            user_id=current_user.id,
            action="submit_orchestrator_run",
            resource_type="orchestrator_run",
            resource_id=str(run.id),
            details={"goal_preview": (payload.goal or "")[:200], "has_url": bool(payload.target_url), "has_artifact": bool(payload.artifact_id)},
            ip_address=_client_ip(request),
        )

        logger.info("Orchestrator: run %s submitted by %s", run.id, current_user.id)
        return _run_out(run)

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Orchestrator: submit run DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/runs/{run_id}", response_model=OrchestratorRunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    run = db.query(OrchestratorRun).filter(OrchestratorRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    decisions = (
        db.query(OrchestratorStepDecision)
        .filter(OrchestratorStepDecision.run_id == run.id)
        .order_by(OrchestratorStepDecision.sequence)
        .all()
    )
    return _run_out(run, decisions)


@router.post("/runs/{run_id}/cancel", response_model=OrchestratorRunOut)
def cancel_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()
    run = db.query(OrchestratorRun).filter(OrchestratorRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == OrchestratorRunStatus.pending:
        run.status = OrchestratorRunStatus.cancelled
        db.commit()
        db.refresh(run)
    return _run_out(run)
