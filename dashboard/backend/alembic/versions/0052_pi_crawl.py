"""Project Intelligence — Phase 3: Active Crawler & Visual schema.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §14.3, §16 (table 8),
§24, §27 (table 17: "3 — Active Crawler & Visual") and the answers recorded
against §28 (table 18) Q1/Q2/Q7 during Phase 3 sign-off:

  Q1 (crawl-target restriction): NOT inferred from the free-text environment
     label ("staging"/"production" as strings). Recorded explicitly, per
     environment, by whoever can already write project_environments rows
     (Admin/QA Lead — require_permission("projects"), unchanged). This
     migration adds project_environments.is_production for exactly that.
  Q2 (pilot scope): off everywhere by default, opt-in per project. This
     migration adds projects.pi_crawl_enabled (default false) for the
     per-project opt-in, separate from the PI_CRAWL_ENABLED global kill
     switch (an env var, not a column — table 16).
  Q7 (retention window): 90 days, spec default — PI_ARTIFACT_RETENTION_DAYS
     (an env var — see services/pi_ingest.py — not a column here).

Also adds projects.pi_crawl_production_approved: spec §14.3/§24's own
baseline design ("Default target is staging. Production crawling is
opt-in per project and recorded on the project record" / "production
crawling requires explicit per-project sign-off recorded on the
project"). Combined with is_production above, the crawl scheduler's gate
for a given (project, environment) pair is:

  PI_CRAWL_ENABLED (env)
    AND projects.pi_crawl_enabled
    AND project_environments.base_url IS NOT NULL
    AND (NOT project_environments.is_production
         OR projects.pi_crawl_production_approved)

pi_design_patterns (spec table 8, Phase 3): colour/typography/layout/
component-style conventions observed by one bounded vision-tier LLM call
per crawl screenshot (services/pi_crawl.py). Modeled like pi_behavior_notes
(description/confidence/status) with the two fields table 8 asks for beyond
that (pattern_type, value) plus evidence_ref — a path under VISUAL_DATA_DIR,
never a duplicated blob (spec §16: "Screenshots are referenced, never
duplicated"), cleaned up by the new retention task (services/pi_crawl.py /
workers/tasks/pi_crawl.py) rather than by this migration.

No existing table, column, or migration is touched. All three new booleans
default to false, so every existing project/environment row is
unaffected until an Admin/QA Lead explicitly opts in — same "off until
deliberately turned on" posture as PI_HEAL_LEDGER in Phase 2.

Revision ID: 0052_pi_crawl
Revises: 0051_pi_drift_flags
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_pi_crawl"
down_revision: Union[str, None] = "0051_pi_drift_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helpers (matches 0049/0050/0051's established convention) ───

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

    # ── projects: additive Phase 3 columns ───────────────────────────────
    if not _column_exists("projects", "pi_crawl_enabled"):
        op.add_column(
            "projects",
            sa.Column("pi_crawl_enabled", sa.Boolean, nullable=False, server_default="false"),
        )
    if not _column_exists("projects", "pi_crawl_production_approved"):
        op.add_column(
            "projects",
            sa.Column(
                "pi_crawl_production_approved", sa.Boolean, nullable=False,
                server_default="false",
            ),
        )

    # ── project_environments: additive Phase 3 column ────────────────────
    if not _column_exists("project_environments", "is_production"):
        op.add_column(
            "project_environments",
            sa.Column("is_production", sa.Boolean, nullable=False, server_default="false"),
        )

    # ── pi_design_patterns ────────────────────────────────────────────────
    if not _table_exists("pi_design_patterns"):
        op.create_table(
            "pi_design_patterns",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            # Best-effort context, not part of this pattern's identity — a
            # crawl screenshot is not reliably attributable to one catalogued
            # screen at capture time (see services/pi_crawl.py), so this is
            # nullable and never joined on for uniqueness.
            sa.Column("screen_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_screens.id", ondelete="SET NULL"), nullable=True),
            # 'color' | 'typography' | 'layout' | 'component_style' (spec
            # table 7: "Colour, typography, layout conventions"). Plain
            # string, not an enum -- a display categorisation the vision
            # prompt's own wording governs, not a state machine.
            sa.Column("pattern_type", sa.String(50), nullable=False),
            # The observed detail itself (e.g. {"hex": "#1a73e8", "usage":
            # "primary button background"}) -- free-form because "value" is
            # necessarily shaped differently per pattern_type.
            sa.Column("value", postgresql.JSONB, nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            # Path under VISUAL_DATA_DIR to the screenshot this pattern was
            # read from -- a pointer, never a duplicated blob (spec §16).
            # Nullable because the retention cleanup task clears this once
            # the underlying file has aged out (PI_ARTIFACT_RETENTION_DAYS),
            # rather than deleting the (still-valid) knowledge row with it.
            sa.Column("evidence_ref", sa.Text, nullable=True),
            sa.Column("confidence", sa.Float, nullable=True),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_design_patterns_project_id", "pi_design_patterns", ["project_id"])
        op.create_index("ix_pi_design_patterns_screen_id", "pi_design_patterns", ["screen_id"])
        op.create_index("ix_pi_design_patterns_status", "pi_design_patterns", ["status"])


def downgrade() -> None:
    if _table_exists("pi_design_patterns"):
        op.drop_table("pi_design_patterns")
    if _column_exists("project_environments", "is_production"):
        op.drop_column("project_environments", "is_production")
    if _column_exists("projects", "pi_crawl_production_approved"):
        op.drop_column("projects", "pi_crawl_production_approved")
    if _column_exists("projects", "pi_crawl_enabled"):
        op.drop_column("projects", "pi_crawl_enabled")
