"""Project Intelligence — Phase 1 foundation schema.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §16 for the full
design. Short version: a per-project, human-verified knowledge store that
AEP's existing capture points (Robot Framework runs, Vibe/AI runs, document
ingestion) feed into, and that `app.services.flow_validation.get_flow_model`
and `app.services.pi_context.build_project_brief` (Phase 4) read back from.

Tables in this revision (Phase 1 — Foundation & Flow only; pi_drift_flags,
pi_design_patterns and pi_embeddings are Phase 2/3/5 and ship in later
revisions, not here):

  pi_screens             catalog of discovered screens (pending/verified/rejected)
  pi_navigation_edges    observed navigation graph between screens
  pi_components          control inventory + proven-locator map, keyed by a
                          stable component_key so a label change is
                          detectable as a rename rather than a delete+add
  pi_behavior_notes      plain-language behaviour descriptions per screen
  pi_capture_events      raw ingestion queue — the replay/debug audit trail
  pi_change_log          append-only history per entity
  pi_review_actions      who approved/edited/rejected what, and why

pi_flows (the table the get_flow_model seam actually reads) is deliberately
a SEPARATE revision (0050) — see that file's docstring for why.

Every knowledge-bearing table carries project_id (FK -> projects.id, ON
DELETE CASCADE, indexed) -- this is the project-isolation guarantee. Every
such table also carries a `status` column using the shared pi_status_enum
(pending, verified, rejected, superseded), mirroring the
SowSectionStatus / needs_review pattern already in the codebase (see
app/models/sow.py) so the review UI behaves identically to SOW review.

Every table here is DERIVED and rebuildable from captures and runs already
stored elsewhere in AEP (matching project_ui_inventory's own migration
docstring on this point) -- dropping this schema loses no source data.

Revision ID: 0049_project_intelligence_foundation
Revises: 0048_sow_document_slugs
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_pi_foundation"
down_revision: Union[str, None] = "0048_sow_document_slugs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helpers (matches 0013/0021/0028's established convention) ───

def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.fetchone() is not None


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # ── Shared status enum ───────────────────────────────────────────────
    # Raw SQL so SQLAlchemy never auto-emits a second CREATE TYPE when the
    # table below is created (same reasoning as 0028_sow_foundation).
    if not _enum_exists("pi_status_enum"):
        op.execute(
            "CREATE TYPE pi_status_enum AS ENUM "
            "('pending', 'verified', 'rejected', 'superseded')"
        )

    pi_status = postgresql.ENUM(
        "pending", "verified", "rejected", "superseded",
        name="pi_status_enum", create_type=False,
    )

    # ── pi_screens ────────────────────────────────────────────────────────
    if not _table_exists("pi_screens"):
        op.create_table(
            "pi_screens",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("environment_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("project_environments.id", ondelete="SET NULL"),
                       nullable=True),
            sa.Column("route", sa.String(500), nullable=False),
            sa.Column("title", sa.String(300), nullable=True),
            sa.Column("description", sa.Text, nullable=True),
            # Where this row's current content came from: 'rf' | 'vibe' |
            # 'crawl' | 'document'. Deliberately a plain string, not a DB
            # enum -- matches the SOW_SOURCE_STAGE_* precedent
            # (app/models/sow.py): a display/provenance tag that must never
            # need a migration to extend.
            sa.Column("source_type", sa.String(30), nullable=False),
            # sha256 of the normalized observation used to produce this
            # row's description -- lets pi_extract skip re-summarizing a
            # screen whose evidence hasn't changed, the same idempotency
            # key strategy ui_inventory.py uses for source_artifact_ids.
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            # Set when a human edit or a later observation supersedes this
            # row -- the row is kept (history is never overwritten), a new
            # row is inserted, and this FK points at it.
            sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="SET NULL"), nullable=True),
            sa.Column("first_seen_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_screens_project_id", "pi_screens", ["project_id"])
        op.create_index("ix_pi_screens_status", "pi_screens", ["status"])
        # Partial unique index on the ACTIVE claim about a route -- not
        # "status <> 'rejected'": superseded rows must be free to
        # accumulate (that's the audit trail), only one pending-or-verified
        # row per (project, environment, route) may exist at a time. This
        # is the structural defence behind entity resolution described in
        # spec §16, not just an LLM check.
        op.create_index(
            "ux_pi_screens_active_route",
            "pi_screens",
            ["project_id", "environment_id", "route"],
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'verified')"),
        )

    # ── pi_navigation_edges ───────────────────────────────────────────────
    if not _table_exists("pi_navigation_edges"):
        op.create_table(
            "pi_navigation_edges",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False),
            sa.Column("to_screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False),
            sa.Column("trigger_action", sa.String(300), nullable=True),
            sa.Column("observed_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_observed_at", sa.DateTime, nullable=True),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_navigation_edges_project_id", "pi_navigation_edges", ["project_id"])
        op.create_index(
            "ix_pi_navigation_edges_from_to", "pi_navigation_edges",
            ["from_screen_id", "to_screen_id"],
        )

    # ── pi_components ─────────────────────────────────────────────────────
    if not _table_exists("pi_components"):
        op.create_table(
            "pi_components",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False),
            # Deterministic hash over (screen_id, component_type, anchor) --
            # see spec §18.2. This is what makes a rename detectable: the
            # key stays stable across a label change as long as the anchor
            # (data-testid / id / name / aria-label+position) is stable.
            sa.Column("component_key", sa.String(128), nullable=False),
            # 1-3 = stable anchor (survives a rename), 4 = partial, 5 = text
            # only (does NOT survive a rename -- spec §18.4). Stored so the
            # UI can show how much to trust a `label_changed` flag.
            sa.Column("identity_tier", sa.SmallInteger, nullable=False),
            sa.Column("component_type", sa.String(30), nullable=False),
            sa.Column("label", sa.String(500), nullable=False),
            # Populated only while a label_changed change is pending review
            # -- lets the review UI show the old -> new diff without a join
            # against pi_change_log.
            sa.Column("previous_label", sa.String(500), nullable=True),
            sa.Column("locator", sa.Text, nullable=True),
            sa.Column("locator_strategy", sa.String(50), nullable=True),
            sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            sa.Column("last_seen_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_components_project_id", "pi_components", ["project_id"])
        op.create_index("ix_pi_components_screen_id", "pi_components", ["screen_id"])
        op.create_index(
            "ux_pi_components_active_key",
            "pi_components",
            ["project_id", "component_key"],
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'verified')"),
        )

    # ── pi_behavior_notes ─────────────────────────────────────────────────
    if not _table_exists("pi_behavior_notes"):
        op.create_table(
            "pi_behavior_notes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("source_type", sa.String(30), nullable=False),
            sa.Column("source_ref", sa.String(500), nullable=True),
            sa.Column("confidence", sa.Float, nullable=True),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_behavior_notes_project_id", "pi_behavior_notes", ["project_id"])
        op.create_index("ix_pi_behavior_notes_screen_id", "pi_behavior_notes", ["screen_id"])

    # ── pi_capture_events ─────────────────────────────────────────────────
    if not _table_exists("pi_capture_events"):
        op.create_table(
            "pi_capture_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_type", sa.String(30), nullable=False),
            # Soft reference -- the id of a test_run row (RF) or an
            # ai_test_runs row (Vibe). No FK: the two source tables are
            # disjoint and a single column can't target either cleanly,
            # matching how pi_change_log.detected_by_run_id is also a soft
            # reference (spec §16).
            sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            # Path to a JSON sidecar for large payloads (the RF listener's
            # keyword-capture buffer, per spec §14.1) -- never a binary,
            # never a screenshot; those stay referenced on VISUAL_DATA_DIR
            # exactly as Visual QA and run video already do.
            sa.Column("payload_ref", sa.Text, nullable=True),
            # Small payloads (a handful of navigation events) can be stored
            # inline instead of round-tripping through a file.
            sa.Column("payload_json", postgresql.JSONB, nullable=True),
            sa.Column("processed_at", sa.DateTime, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_capture_events_project_id", "pi_capture_events", ["project_id"])
        # Partial index on the queue itself: unprocessed rows are what the
        # worker scans on every tick, and this table accumulates one row
        # per run indefinitely.
        op.create_index(
            "ix_pi_capture_events_unprocessed", "pi_capture_events", ["created_at"],
            postgresql_where=sa.text("processed_at IS NULL"),
        )

    # ── pi_change_log ─────────────────────────────────────────────────────
    if not _table_exists("pi_change_log"):
        op.create_table(
            "pi_change_log",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            # 'screen' | 'component' | 'behavior_note' | 'flow'. Polymorphic
            # by design -- entity_id has no FK because it targets a
            # different table per entity_type (same pattern pi_review_actions
            # uses below, and the pattern pi_drift_flags will use in Phase 2).
            sa.Column("entity_type", sa.String(30), nullable=False),
            sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
            # 'added' | 'removed' | 'label_changed' | 'behavior_changed' |
            # 'locator_broken' | 'candidate_rename' (spec §18.3). Plain
            # string, not a DB enum -- this taxonomy is expected to grow as
            # Phase 2 ships drift detection, and must not require a
            # migration to extend (same reasoning as source_type above).
            sa.Column("change_type", sa.String(30), nullable=False),
            sa.Column("previous_value", postgresql.JSONB, nullable=True),
            sa.Column("new_value", postgresql.JSONB, nullable=True),
            sa.Column("detected_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("detected_by_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_change_log_project_id", "pi_change_log", ["project_id"])
        op.create_index(
            "ix_pi_change_log_entity", "pi_change_log", ["entity_type", "entity_id"],
        )

    # ── pi_review_actions ─────────────────────────────────────────────────
    if not _table_exists("pi_review_actions"):
        op.create_table(
            "pi_review_actions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entity_type", sa.String(30), nullable=False),
            sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            # Required by the API layer for 'reject' (see
            # api/v1/project_intelligence.py); nullable here because
            # 'approve' and 'edit' carry none.
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_review_actions_project_id", "pi_review_actions", ["project_id"])
        op.create_index(
            "ix_pi_review_actions_entity", "pi_review_actions", ["entity_type", "entity_id"],
        )
        op.create_index("ix_pi_review_actions_actor", "pi_review_actions", ["actor_user_id"])


def downgrade() -> None:
    for table in (
        "pi_review_actions",
        "pi_change_log",
        "pi_capture_events",
        "pi_behavior_notes",
        "pi_components",
        "pi_navigation_edges",
        "pi_screens",
    ):
        if _table_exists(table):
            op.drop_table(table)
    if _enum_exists("pi_status_enum"):
        op.execute("DROP TYPE pi_status_enum")
