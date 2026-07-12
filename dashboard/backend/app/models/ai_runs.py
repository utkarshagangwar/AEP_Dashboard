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
    """A reusable skill in the Vibe Testing "Skills" tab — either a recorded
    action replay or a prompt-only instruction, distinguished by whether
    history_json is set.

    Recorded skills: history_json stores the browser-use AgentHistoryList
    (screenshots stripped) captured from a passed goal-based AI test run, so
    the run can be replayed via Agent.rerun_history() without any LLM
    planning calls.

    Prompt skills: history_json is None. Saved directly from SOW/video
    checkpoint parsing (see app.services.skill_store) — a detailed,
    step-by-step instruction an AI agent can execute, with no live browser
    run required to produce it. Running one (from the Skills tab) is a
    normal AI-planned run; if it passes, the goal-based auto-save path
    upgrades this same row (matched by goal_hash) with a real recording.

    A skill can also be viewed/edited by hand from the Skills tab (name,
    goal text, project). Editing sets manually_edited=True and, if the goal
    text changed on a recorded skill, clears history_json/step_count — the
    old recording no longer matches the edited instructions, so the next
    run re-plans with AI and records fresh actions instead of silently
    replaying stale ones.
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
    # Set only for a prompt skill extracted from a SOW/video checkpoint.
    # SET NULL (not CASCADE) on document delete — a skill may have already
    # been run and upgraded to a recorded one, decoupled from its source doc.
    source_type = mapped_column(String(20), nullable=True)  # "sow" | "video" | null
    source_artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable identity for prompt-skill upserts (artifact_id + normalized
    # checkpoint title) — re-analyzing a part updates this row in place
    # instead of duplicating it. Null for goal-based recorded skills, which
    # upsert by goal_hash instead.
    source_key = mapped_column(String(300), nullable=True, unique=True, index=True)
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
    history_json = mapped_column(Text, nullable=True)
    step_count = mapped_column(Integer, default=0)
    times_replayed = mapped_column(Integer, default=0, nullable=False)
    last_replay_status = mapped_column(String(20), nullable=True)
    last_replayed_at = mapped_column(DateTime, nullable=True)
    # True once a human has edited name/goal/project via the Skills tab.
    # Protects that edit from being silently clobbered the next time the
    # source SOW/video part is re-analyzed (see skill_store.upsert_prompt_skill).
    manually_edited = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def has_recording(self) -> bool:
        return self.history_json is not None


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
