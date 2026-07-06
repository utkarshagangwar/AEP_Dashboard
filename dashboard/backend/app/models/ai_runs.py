"""ORM models for AI test run tables."""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base


class AIRunStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"
    inconclusive = "inconclusive"
    cancelled = "cancelled"


class AIEventStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"


class AIStepType(str, PyEnum):
    deterministic = "deterministic"
    ai_scoped = "ai_scoped"


class AICredentialProfile(Base):
    __tablename__ = "ai_credential_profiles"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = mapped_column(String(200), nullable=False)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    allowed_domains = mapped_column(JSONB, nullable=True)
    credentials_json = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AISkill(Base):
    """Reusable action recording captured from a passed AI test run.

    history_json stores the browser-use AgentHistoryList (screenshots
    stripped) so the run can be replayed via Agent.rerun_history() without
    any LLM planning calls.
    """

    __tablename__ = "ai_skills"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = mapped_column(String(300), nullable=False)
    goal = mapped_column(Text, nullable=False)
    goal_hash = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    environment = mapped_column(String(200), nullable=True)
    credential_profile_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    history_json = mapped_column(Text, nullable=False)
    step_count = mapped_column(Integer, default=0)
    times_replayed = mapped_column(Integer, default=0, nullable=False)
    last_replay_status = mapped_column(String(20), nullable=True)
    last_replayed_at = mapped_column(DateTime, nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AITestRun(Base):
    __tablename__ = "ai_test_runs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goal = mapped_column(Text, nullable=False)
    environment = mapped_column(String(200), nullable=True)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_profile_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    credential_profile_name = mapped_column(String(200), nullable=True)
    status = mapped_column(
        Enum(AIRunStatus, name="ai_run_status_enum"),
        nullable=False,
        default=AIRunStatus.pending,
    )
    started_at = mapped_column(DateTime, nullable=True)
    completed_at = mapped_column(DateTime, nullable=True)
    duration_ms = mapped_column(Integer, nullable=True)
    step_count = mapped_column(Integer, default=0)
    summary = mapped_column(Text, nullable=True)
    raw_summary = mapped_column(Text, nullable=True)
    run_type = mapped_column(String(20), nullable=False, server_default="ai")
    skill_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_skills.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    failing_step_index = mapped_column(Integer, nullable=True)
    failing_step_description = mapped_column(Text, nullable=True)
    failing_step_screenshot_url = mapped_column(Text, nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AIRunEvent(Base):
    __tablename__ = "ai_run_events"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_test_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = mapped_column(Integer, nullable=False)
    status = mapped_column(
        Enum(AIEventStatus, name="ai_event_status_enum"),
        nullable=False,
        default=AIEventStatus.pending,
    )
    description = mapped_column(Text, nullable=False)
    step_type = mapped_column(
        Enum(AIStepType, name="ai_step_type_enum"),
        nullable=False,
        default=AIStepType.deterministic,
    )
    elapsed_ms = mapped_column(Integer, nullable=True)
    screenshot_url = mapped_column(Text, nullable=True)
    highlighted_element = mapped_column(JSONB, nullable=True)
    is_failing_step = mapped_column(Boolean, default=False)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
