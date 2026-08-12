"""ORM models for Project Intelligence — Phase 1 (Foundation & Flow),
Phase 2 (Change Detection & Healing), Phase 3 (Active Crawler & Visual),
and Phase 5 (Scale — pgvector/semantic search).

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §16-19 for the full
design and app/services/flow_validation.py's module docstring for why
pi_flows is the table that matters most here: `get_flow_model()` reads it
directly (see services/pi_flow.py).

Tables:

  PiScreen             catalog of discovered screens
  PiNavigationEdge      observed navigation graph between screens
  PiComponent           control inventory + proven-locator map, keyed by a
                        stable component_key (spec §18.2) so a label change
                        is detectable as a rename rather than a delete+add
  PiBehaviorNote        plain-language behaviour descriptions per screen
  PiCaptureEvent        raw ingestion queue
  PiChangeLog           append-only history per entity
  PiReviewAction        who approved/edited/rejected what, and why
  PiFlow                the flow model served by flow_validation.get_flow_model
  PiDriftFlag           spec-vs-reality mismatches and their proposed
                        healing (Phase 2, spec §18-19) — written only for
                        label_changed / behavior_changed / confirmed
                        candidate_rename; see services/pi_drift.py
  PiDesignPattern        colour/typography/layout/component-style
                        conventions observed by the scheduled crawler's
                        bounded vision pass (Phase 3, spec §14.3/table 8);
                        see services/pi_crawl.py
  PiEmbedding            vector embeddings for semantic search over
                        verified PiBehaviorNote rows (Phase 5, spec §16
                        table 8, migration 0053); see
                        services/pi_embed.py. DEFENSIVELY IMPORTED — see
                        the try/except block below.

Additive only: no existing table or model is modified by this file.
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base

# Phase 5 (Scale — pgvector). Every other import in this file is a hard,
# always-installed dependency (sqlalchemy itself); `pgvector` is new,
# added to requirements.txt specifically for this table's `embedding`
# column. Guarded here — not a bare top-level import — because
# app/models/__init__.py imports this whole module EAGERLY on every app
# boot, for every deployment, whether or not that deployment has run
# `pip install -r requirements.txt` since Phase 5 landed or has migration
# 0053 applied yet. An unguarded `from pgvector.sqlalchemy import Vector`
# would turn a stale environment's app boot into a hard crash the moment
# this file exists, for a feature that is opt-in and defaults OFF
# (PI_SEMANTIC_SEARCH_ENABLED) — exactly the kind of regression the
# project's "nothing else may break" requirement rules out. If the import
# fails, PiEmbedding is simply not defined as a class; every caller in
# services/pi_embed.py already checks for that (see its own module
# docstring) before touching this table.
try:
    from pgvector.sqlalchemy import Vector as _PgVector

    _PGVECTOR_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by a stale environment
    _PgVector = None
    _PGVECTOR_AVAILABLE = False


class PiStatus(str, PyEnum):
    """Shared across every Project Intelligence table (migration 0049).

    Mirrors SowSectionStatus / needs_review (app/models/sow.py) so the
    review UI behaves identically to SOW review. `superseded` is set on the
    OLD row when a change is approved — the old row is never overwritten,
    only marked and pointed at its replacement (see *_by_id columns below).
    """

    pending = "pending"
    verified = "verified"
    rejected = "rejected"
    superseded = "superseded"


class PiScreen(Base):
    __tablename__ = "pi_screens"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    environment_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    route = mapped_column(String(500), nullable=False)
    title = mapped_column(String(300), nullable=True)
    description = mapped_column(Text, nullable=True)
    # 'rf' | 'vibe' | 'crawl' | 'document' — see migration docstring for why
    # this stays a plain string rather than a DB enum.
    source_type = mapped_column(String(30), nullable=False)
    content_hash = mapped_column(String(64), nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    superseded_by_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="SET NULL"), nullable=True,
    )
    first_seen_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_seen_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    created_by = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiNavigationEdge(Base):
    __tablename__ = "pi_navigation_edges"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    from_screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False,
    )
    to_screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False,
    )
    trigger_action = mapped_column(String(300), nullable=True)
    observed_count = mapped_column(Integer, nullable=False, default=0)
    last_observed_at = mapped_column(DateTime, nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiComponent(Base):
    __tablename__ = "pi_components"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Deterministic hash over (screen_id, component_type, anchor) — see
    # services/pi_ingest.py:compute_component_key. Stable across a label
    # change as long as the anchor (data-testid/id/name/aria-label+position)
    # is stable; this is what makes a rename detectable at all (spec §18.2).
    component_key = mapped_column(String(128), nullable=False)
    # 1-3 = stable anchor (a label_changed flag is trustworthy), 4 = partial,
    # 5 = text-only (a rename can only ever be a *candidate*, never
    # asserted — spec §18.4).
    identity_tier = mapped_column(SmallInteger, nullable=False)
    component_type = mapped_column(String(30), nullable=False)
    label = mapped_column(String(500), nullable=False)
    previous_label = mapped_column(String(500), nullable=True)
    locator = mapped_column(Text, nullable=True)
    locator_strategy = mapped_column(String(50), nullable=True)
    success_count = mapped_column(Integer, nullable=False, default=0)
    fail_count = mapped_column(Integer, nullable=False, default=0)
    # Phase 2 additive columns (migration 0051) — see services/pi_drift.py.
    # Consecutive screen re-visits in which this component was NOT
    # observed; reset to 0 on any observation. Feeds `removed`
    # classification once it reaches pi_ingest.removed_threshold() (spec
    # table 10: "N is configurable; default 3").
    missed_streak = mapped_column(Integer, nullable=False, default=0)
    # This component's most recently observed success/failure. NULL means
    # "no outcome recorded yet" (including every row created before Phase
    # 2), which deliberately never fires `locator_broken` on its own —
    # only a recorded True -> False transition does (see
    # services/pi_drift.py._classify_locator_broken).
    last_outcome_success = mapped_column(Boolean, nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    last_seen_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiBehaviorNote(Base):
    __tablename__ = "pi_behavior_notes"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    description = mapped_column(Text, nullable=False)
    source_type = mapped_column(String(30), nullable=False)
    source_ref = mapped_column(String(500), nullable=True)
    confidence = mapped_column(Float, nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiCaptureEvent(Base):
    """Raw ingestion queue — the replay/debug audit trail (spec §16)."""

    __tablename__ = "pi_capture_events"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_type = mapped_column(String(30), nullable=False)
    # Soft reference to a test_run or ai_test_runs row — no FK, the two
    # source tables are disjoint (see migration docstring).
    source_run_id = mapped_column(UUID(as_uuid=True), nullable=True)
    payload_ref = mapped_column(Text, nullable=True)
    payload_json = mapped_column(JSONB, nullable=True)
    processed_at = mapped_column(DateTime, nullable=True)
    error = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PiChangeLog(Base):
    """Append-only version history per entity. Never mutated, never pruned
    by application code — only ever inserted into."""

    __tablename__ = "pi_change_log"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 'screen' | 'component' | 'behavior_note' | 'flow' — polymorphic,
    # entity_id has no FK (see migration docstring).
    entity_type = mapped_column(String(30), nullable=False)
    entity_id = mapped_column(UUID(as_uuid=True), nullable=False)
    # 'added' | 'removed' | 'label_changed' | 'behavior_changed' |
    # 'locator_broken' | 'candidate_rename' (spec §18.3).
    change_type = mapped_column(String(30), nullable=False)
    previous_value = mapped_column(JSONB, nullable=True)
    new_value = mapped_column(JSONB, nullable=True)
    detected_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    detected_by_run_id = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PiReviewAction(Base):
    """Who approved/edited/rejected what, and why. Written alongside (never
    instead of) the existing audit log via audit_service.write_audit_log —
    this table is the Project-Intelligence-specific detail; the audit log
    is the platform-wide trail Users and Defects already use."""

    __tablename__ = "pi_review_actions"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    entity_type = mapped_column(String(30), nullable=False)
    entity_id = mapped_column(UUID(as_uuid=True), nullable=False)
    action = mapped_column(String(20), nullable=False)  # 'approve' | 'edit' | 'reject'
    reason = mapped_column(Text, nullable=True)
    actor_user_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PiFlow(Base):
    """The object flow_validation.get_flow_model() reads.

    model_json is exactly the shape build_index()/render_flow_reference()
    already parse — see that module's docstring. locked_behaviours inside
    it is never machine-proposed (enforced in services/pi_flow.py, not
    here) because no observation can prove a behaviour is *permanently*
    unavailable, and a wrong lock silently flags correct checkpoints.
    """

    __tablename__ = "pi_flows"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    environment_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    version = mapped_column(Integer, nullable=False, default=1)
    model_json = mapped_column(JSONB, nullable=False)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    generated_from_run_ids = mapped_column(JSONB, nullable=True)
    edited_by = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    superseded_by_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_flows.id", ondelete="SET NULL"), nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiDriftType(str, PyEnum):
    """Spec §18.3 / table 10 — the closed classification set. All six are
    written to pi_change_log (services/pi_drift.py); only label_changed,
    behavior_changed and confirmed candidate_rename also get a
    PiDriftFlag row (the other three are history, not review work)."""

    label_changed = "label_changed"
    candidate_rename = "candidate_rename"
    locator_broken = "locator_broken"
    removed = "removed"
    added = "added"
    behavior_changed = "behavior_changed"


class PiDriftFlag(Base):
    """Spec-vs-reality mismatches and their proposed healing (spec §18-19,
    Phase 2, migration 0051). See services/pi_drift.py for how a row here
    is created and services/pi_heal.py for how `apply_heal()` consumes one.
    """

    __tablename__ = "pi_drift_flags"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The subject component — the one that changed, or (candidate_rename)
    # the NEW component in the proposed pair.
    component_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_components.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # candidate_rename only — the OLD, apparently-vanished component this
    # flag proposes pairing `component_id` with. NULL for every other type.
    candidate_component_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_components.id", ondelete="CASCADE"), nullable=True,
    )
    # The OLD sow_requirements_ledger row this flag proposes correcting
    # (spec §19.2's matching rule). NULL if no matching fact was found.
    ledger_fact_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("sow_requirements_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    drift_type = mapped_column(
        Enum(PiDriftType, name="pi_drift_type_enum", create_type=False), nullable=False,
    )
    # 'low' | 'medium' | 'high' — deterministic, derived from drift_type +
    # identity_tier at detection time. A display ranking, not a state
    # machine, hence a plain string rather than an enum.
    severity = mapped_column(String(20), nullable=False, default="medium")
    description = mapped_column(Text, nullable=False)
    proposed_label = mapped_column(String(500), nullable=True)
    proposed_behavior_notes = mapped_column(Text, nullable=True)
    # Snapshot at detection time — deliberately not a live join, so this
    # flag's own record does not silently change meaning if the component
    # is edited later.
    identity_tier = mapped_column(SmallInteger, nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    reviewed_by = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at = mapped_column(DateTime, nullable=True)
    # Set by apply_heal() to the id of the NEW ledger row it inserted.
    # Doubles as the single-apply guard (spec §19.4): apply_heal() only
    # ever proceeds while this column is still NULL.
    applied_ledger_fact_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("sow_requirements_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PiDesignPattern(Base):
    """Colour/typography/layout/component-style conventions observed by the
    scheduled crawler's vision pass (spec §14.3, table 7 "UI/design pattern
    extraction", table 8, Phase 3, migration 0052). See services/pi_crawl.py
    for how a row here is produced — one bounded vision-tier LLM call per
    crawl screenshot, capped by PI_CRAWL_MAX_SCREENSHOTS.
    """

    __tablename__ = "pi_design_patterns"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Best-effort context, not part of this pattern's identity — a crawl
    # screenshot cannot be reliably attributed to one catalogued pi_screens
    # row at capture time, so this is never joined on for uniqueness or
    # dedup, only shown as a hint when present.
    screen_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("pi_screens.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # 'color' | 'typography' | 'layout' | 'component_style' — plain string,
    # not an enum, matching pi_change_log.change_type's reasoning: the
    # vision prompt's own wording governs this, not a state machine.
    pattern_type = mapped_column(String(50), nullable=False)
    # The observed detail itself, shaped differently per pattern_type (e.g.
    # {"hex": "#1a73e8", "usage": "primary button background"}).
    value = mapped_column(JSONB, nullable=False)
    description = mapped_column(Text, nullable=True)
    # Path under VISUAL_DATA_DIR to the screenshot this pattern was read
    # from — a pointer, never a duplicated blob (spec §16). Nulled out by
    # the retention cleanup task once the file itself has aged out
    # (PI_ARTIFACT_RETENTION_DAYS); the knowledge row is kept.
    evidence_ref = mapped_column(Text, nullable=True)
    confidence = mapped_column(Float, nullable=True)
    status = mapped_column(
        Enum(PiStatus, name="pi_status_enum", create_type=False),
        nullable=False, default=PiStatus.pending,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )


if _PGVECTOR_AVAILABLE:

    class PiEmbedding(Base):
        """Vector embeddings for semantic search (Phase 5, spec §16 table
        8, migration 0053). See services/pi_embed.py for how a row here is
        produced (Google text-embedding-004, 768 dimensions) and searched
        (pgvector cosine distance).

        Defined only when the `pgvector` package imported successfully —
        see the try/except at the top of this file. On a deployment where
        it did not, `PiEmbedding` simply does not exist as a name in this
        module; app/models/__init__.py's import of it is itself guarded to
        match (see that file).
        """

        __tablename__ = "pi_embeddings"

        id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        project_id = mapped_column(
            UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False, index=True,
        )
        # Polymorphic, no FK — same convention as PiChangeLog.entity_id /
        # PiReviewAction.entity_id. 'behavior_note' is the only value
        # written by Phase 5's own code (services/pi_embed.py); left
        # generic so 'screen'/'component' could be added later without a
        # schema change.
        entity_type = mapped_column(String(30), nullable=False, index=True)
        entity_id = mapped_column(UUID(as_uuid=True), nullable=False)
        # Detects a stale embedding needing regeneration once its source
        # text changes — same purpose as PiScreen.content_hash.
        content_hash = mapped_column(String(64), nullable=True)
        # Dimension fixed at 768 to match text-embedding-004's output size
        # (migration 0053's docstring explains why a different model would
        # need a new migration, not a change to this column).
        embedding = mapped_column(_PgVector(768), nullable=False)
        created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
        updated_at = mapped_column(
            DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
        )
else:  # pragma: no cover - exercised by a stale environment
    PiEmbedding = None
