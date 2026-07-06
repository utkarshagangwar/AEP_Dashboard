"""ORM models for the Orchestrator ("The Brain").

Tables:
  orchestrator_runs          — one orchestrated run per row: the goal/URL/
                                design-reference the user submitted, the
                                resulting status, and denormalized links to
                                whichever sub-agent runs it produced.
  orchestrator_step_decisions — the routing audit trail for a run: which of
                                Hands/Judge/self-execute were invoked or
                                skipped, which model was chosen, and whether
                                the decision was rule-based or classifier-based.

Deliberately does NOT duplicate AIRunEvent/VisualFinding — those are
substantial, already-working, already-polled models. When the orchestrator
invokes Hands or Judge, it creates a real AITestRun/VisualRun row (see
app.services.orchestrator._run_hands/_run_judge) and links to it here by
nullable FK, so existing detail views/polling logic keep working unchanged.

Additive only: no existing table or model is modified.
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base


class OrchestratorRunStatus(str, PyEnum):
    pending = "pending"
    planning = "planning"    # the coordinator is deciding the route
    running = "running"      # a sub-agent (Hands/Judge) is executing, or self-execution is in flight
    passed = "passed"
    failed = "failed"
    partial = "partial"      # e.g. Hands succeeded but Judge was skipped due to a provider outage
    error = "error"
    cancelled = "cancelled"


class OrchestratorStep(str, PyEnum):
    hands = "hands"
    judge = "judge"
    self_execute = "self_execute"


class OrchestratorRun(Base):
    __tablename__ = "orchestrator_runs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    goal = mapped_column(Text, nullable=True)              # None if judge-only run
    target_url = mapped_column(Text, nullable=True)        # None if self-execute/text-only
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_profile_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment = mapped_column(String(200), nullable=True)
    status = mapped_column(
        Enum(OrchestratorRunStatus, name="orchestrator_run_status_enum", create_type=False),
        nullable=False,
        default=OrchestratorRunStatus.pending,
    )
    # Denormalized links to the sub-agent runs this orchestration produced —
    # the UI/API can deep-link to the existing AITestRun/VisualRun detail
    # views (and reuse their polling/SSE logic) without duplicating data here.
    ai_test_run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_test_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visual_run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visual_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    self_execute_answer = mapped_column(Text, nullable=True)
    summary = mapped_column(Text, nullable=True)
    error_message = mapped_column(Text, nullable=True)
    started_at = mapped_column(DateTime, nullable=True)
    completed_at = mapped_column(DateTime, nullable=True)
    duration_ms = mapped_column(Integer, nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OrchestratorStepDecision(Base):
    __tablename__ = "orchestrator_step_decisions"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrator_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step = mapped_column(
        Enum(OrchestratorStep, name="orchestrator_step_enum", create_type=False),
        nullable=False,
    )
    invoked = mapped_column(Boolean, nullable=False, default=False)  # False = considered but skipped
    model_provider = mapped_column(String(50), nullable=True)
    model_name = mapped_column(String(200), nullable=True)
    is_deterministic = mapped_column(Boolean, nullable=False, default=True)  # False = decided by the classifier LLM call
    rationale = mapped_column(Text, nullable=False)
    sequence = mapped_column(Integer, nullable=False, default=0)  # display order
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
