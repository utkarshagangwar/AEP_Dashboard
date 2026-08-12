"""Project Intelligence — Phase 2: Change Detection & Healing schema.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §18-19 and §26.1/§27
(table 17: "2 — Change Detection & Healing"). This revision adds exactly
what Phase 2 needs and nothing else — pi_design_patterns (§14.3, Phase 3)
and pi_embeddings (§16 table 8, Phase 5) still ship in later revisions.

  pi_drift_flags   spec-vs-reality mismatches and their proposed healing
                    (§18.3: written only for label_changed, behavior_changed,
                    and confirmed candidate_rename — the other three
                    classifications this revision's identity-tracking
                    columns support (added/removed/locator_broken) are
                    logged to the existing pi_change_log table only, per
                    spec §18.3's "the others are history, not review work")

Two ADDITIVE columns land on the existing pi_components table (created in
0049) to support classification (services/pi_drift.py, Phase 2):

  missed_streak         consecutive screen re-visits in which this
                         component was NOT observed — feeds `removed`
                         classification (spec table 10: "N is configurable;
                         default 3, so one flaky run cannot delete a
                         control").
  last_outcome_success   this component's most recent observed
                         success/failure — feeds `locator_broken`
                         classification as an edge-transition (success ->
                         failure), not a per-run repeat, so one flaky run
                         does not spam the review queue either.

Both default to a value that makes an old (pre-Phase-2) row behave exactly
as if it had just been freshly observed (missed_streak=0,
last_outcome_success=NULL, meaning "no prior outcome recorded, first
observation under Phase 2 cannot be a broken-transition"), so no backfill
is required and no existing row's behaviour changes on upgrade.

No existing table, column, or migration is touched. sow_requirements_ledger
gains two new inbound FKs from pi_drift_flags (ledger_fact_id,
applied_ledger_fact_id) -- both ON DELETE SET NULL, so this migration can
never block or cascade-affect a SOW deletion (spec §19's healing write
itself is a separate, human-triggered runtime operation in
services/pi_heal.py -- this file only creates the schema it writes to).

Revision ID: 0051_pi_drift_flags
Revises: 0050_pi_flows
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_pi_drift_flags"
down_revision: Union[str, None] = "0050_pi_flows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helpers (matches 0049/0050's established convention) ────────

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


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # pi_status_enum was created in 0049 and stays shared across every
    # Project Intelligence table -- create_type=False so this migration
    # never tries to redefine it.
    pi_status = postgresql.ENUM(
        "pending", "verified", "rejected", "superseded",
        name="pi_status_enum", create_type=False,
    )

    # ── pi_drift_type_enum ───────────────────────────────────────────────
    # Deliberately a DB enum here (unlike pi_change_log.change_type, which
    # is a plain string) -- pi_drift_flags is the human review surface, and
    # a closed set of six values matching spec table 10 exactly is worth
    # the migration cost of extending it later; pi_change_log stays a
    # string because it is an append-only history sink that must never
    # block on a schema change.
    if not _enum_exists("pi_drift_type_enum"):
        op.execute(
            "CREATE TYPE pi_drift_type_enum AS ENUM "
            "('label_changed', 'candidate_rename', 'locator_broken', "
            "'removed', 'added', 'behavior_changed')"
        )

    pi_drift_type = postgresql.ENUM(
        "label_changed", "candidate_rename", "locator_broken",
        "removed", "added", "behavior_changed",
        name="pi_drift_type_enum", create_type=False,
    )

    # ── pi_components: additive Phase 2 columns ──────────────────────────
    if not _column_exists("pi_components", "missed_streak"):
        op.add_column(
            "pi_components",
            sa.Column("missed_streak", sa.Integer, nullable=False, server_default="0"),
        )
    if not _column_exists("pi_components", "last_outcome_success"):
        op.add_column(
            "pi_components",
            sa.Column("last_outcome_success", sa.Boolean, nullable=True),
        )

    # ── pi_drift_flags ────────────────────────────────────────────────────
    if not _table_exists("pi_drift_flags"):
        op.create_table(
            "pi_drift_flags",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="CASCADE"), nullable=False),
            # The subject component: the one whose label/locator/behaviour
            # changed, or -- for candidate_rename -- the NEW component in
            # the pair (see candidate_component_id below).
            sa.Column("component_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_components.id", ondelete="CASCADE"), nullable=False),
            # candidate_rename only (spec §18.3/table 10): the OLD,
            # apparently-vanished tier-5 component this flag proposes
            # pairing `component_id` with. NULL for every other drift_type.
            sa.Column("candidate_component_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_components.id", ondelete="CASCADE"), nullable=True),
            # The OLD sow_requirements_ledger row this flag proposes
            # correcting (spec §19.2's matching rule) -- NULL if no
            # matching ledger fact was found at detection time (there is
            # nothing to heal, but the drift itself is still worth
            # surfacing for review). SET NULL, never CASCADE: a ledger row
            # is never deleted by this feature (see services/pi_heal.py),
            # only superseded, so this should not fire in practice, but a
            # flag must survive even if it somehow did.
            sa.Column("ledger_fact_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("sow_requirements_ledger.id", ondelete="SET NULL"),
                       nullable=True),
            sa.Column("drift_type", pi_drift_type, nullable=False),
            # 'low' | 'medium' | 'high' -- deterministic, derived from
            # drift_type + identity_tier at detection time (see
            # services/pi_drift.py). Plain string, not an enum: a display
            # ranking, not a state machine.
            sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
            # Human-readable explanation. LLM-assisted (spec §19.2: "The
            # LLM is used only to write the human-readable explanation and
            # the proposed corrected behavior_notes") with a deterministic
            # fallback -- never blocks flag creation on a model call.
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("proposed_label", sa.String(500), nullable=True),
            sa.Column("proposed_behavior_notes", sa.Text, nullable=True),
            # Snapshot of the component's identity_tier at detection time --
            # deliberately NOT a live join to pi_components, so a flag's
            # own review record does not silently change meaning if the
            # component row is edited later.
            sa.Column("identity_tier", sa.SmallInteger, nullable=True),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            sa.Column("reviewed_by", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime, nullable=True),
            # The id of the NEW sow_requirements_ledger row inserted by
            # apply_heal() (services/pi_heal.py, spec §19.3 step 2). Doubles
            # as the single-apply guard: apply_heal() only ever proceeds
            # when this column is still NULL (spec §19.4).
            sa.Column("applied_ledger_fact_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("sow_requirements_ledger.id", ondelete="SET NULL"),
                       nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_drift_flags_project_id", "pi_drift_flags", ["project_id"])
        op.create_index("ix_pi_drift_flags_screen_id", "pi_drift_flags", ["screen_id"])
        op.create_index("ix_pi_drift_flags_component_id", "pi_drift_flags", ["component_id"])
        op.create_index("ix_pi_drift_flags_status", "pi_drift_flags", ["status"])


def downgrade() -> None:
    if _table_exists("pi_drift_flags"):
        op.drop_table("pi_drift_flags")
    if _enum_exists("pi_drift_type_enum"):
        op.execute("DROP TYPE pi_drift_type_enum")
    if _column_exists("pi_components", "last_outcome_success"):
        op.drop_column("pi_components", "last_outcome_success")
    if _column_exists("pi_components", "missed_streak"):
        op.drop_column("pi_components", "missed_streak")
