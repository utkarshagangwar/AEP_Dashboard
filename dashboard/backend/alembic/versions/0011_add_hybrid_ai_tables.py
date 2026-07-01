"""Add Hybrid AI Testing tables: hybrid_ai_phases, hybrid_ai_configs, ai_step_annotations

Revision ID: 0011_hybrid_ai
Revises: 0010_result_details
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_hybrid_ai"
down_revision: Union[str, None] = "0010_result_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    return result.fetchone() is not None


def _enum_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :name"),
        {"name": name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    if not _enum_exists("phase_status_enum"):
        op.execute(
            "CREATE TYPE phase_status_enum AS ENUM "
            "('not_started', 'in_progress', 'complete')"
        )
    if not _enum_exists("ai_step_outcome_enum"):
        op.execute(
            "CREATE TYPE ai_step_outcome_enum AS ENUM "
            "('success', 'failure', 'timeout')"
        )

    # ── Table 1: hybrid_ai_phases ─────────────────────────────────────────────
    if not _table_exists("hybrid_ai_phases"):
        op.create_table(
            "hybrid_ai_phases",
            sa.Column("phase_number", sa.SmallInteger(), primary_key=True),
            sa.Column("phase_name", sa.String(200), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "not_started", "in_progress", "complete",
                    name="phase_status_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="not_started",
            ),
            sa.Column("completion_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        # Seed the 5 phases from the document
        op.execute(
            sa.text(
                """
                INSERT INTO hybrid_ai_phases (phase_number, phase_name, status) VALUES
                (1, 'Shared CDP Session Pattern',              'not_started'),
                (2, 'Credential & Safety Bounds',             'not_started'),
                (3, 'Scoped Agent Helper',                    'not_started'),
                (4, 'Identify & Convert Unstable Test Points','not_started'),
                (5, 'Reporting & Visibility in AEP',          'not_started')
                """
            )
        )

    # ── Table 2: hybrid_ai_configs ────────────────────────────────────────────
    if not _table_exists("hybrid_ai_configs"):
        op.create_table(
            "hybrid_ai_configs",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("allowed_domains", postgresql.JSONB(), nullable=True),
            sa.Column("pinned_browser_use_version", sa.String(50), nullable=True),
            sa.Column(
                "max_steps_cap", sa.SmallInteger(), nullable=False, server_default="5"
            ),
            sa.Column("safety_tests_status", postgresql.JSONB(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_hybrid_ai_configs_project_id",
            "hybrid_ai_configs",
            ["project_id"],
        )

    # ── Table 3: ai_step_annotations ─────────────────────────────────────────
    if not _table_exists("ai_step_annotations"):
        op.create_table(
            "ai_step_annotations",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "test_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "test_result_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("test_results.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("suite_name", sa.String(500), nullable=True),
            sa.Column("test_name", sa.String(500), nullable=True),
            sa.Column("step_index", sa.Integer(), nullable=True),
            sa.Column("agent_task", sa.Text(), nullable=False),
            sa.Column(
                "outcome",
                postgresql.ENUM(
                    "success", "failure", "timeout",
                    name="ai_step_outcome_enum",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("action_summary", postgresql.JSONB(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_ai_step_annotations_test_run_id",
            "ai_step_annotations",
            ["test_run_id"],
        )
        op.create_index(
            "ix_ai_step_annotations_test_result_id",
            "ai_step_annotations",
            ["test_result_id"],
        )


def downgrade() -> None:
    if _table_exists("ai_step_annotations"):
        op.drop_table("ai_step_annotations")
    if _table_exists("hybrid_ai_configs"):
        op.drop_table("hybrid_ai_configs")
    if _table_exists("hybrid_ai_phases"):
        op.drop_table("hybrid_ai_phases")
    if _enum_exists("ai_step_outcome_enum"):
        op.execute("DROP TYPE ai_step_outcome_enum")
    if _enum_exists("phase_status_enum"):
        op.execute("DROP TYPE phase_status_enum")
