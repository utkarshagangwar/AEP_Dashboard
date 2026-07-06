"""Add Orchestrator tables: orchestrator_runs, orchestrator_step_decisions

Revision ID: 0015_orchestrator
Revises: 0014_artifact_parse_status
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_orchestrator"
down_revision: Union[str, None] = "0014_artifact_parse_status"
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


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # ── Enums (raw SQL so SQLAlchemy never auto-emits CREATE TYPE) ──────────
    if not _enum_exists("orchestrator_run_status_enum"):
        op.execute(
            "CREATE TYPE orchestrator_run_status_enum AS ENUM "
            "('pending', 'planning', 'running', 'passed', 'failed', "
            "'partial', 'error', 'cancelled')"
        )
    if not _enum_exists("orchestrator_step_enum"):
        op.execute(
            "CREATE TYPE orchestrator_step_enum AS ENUM "
            "('hands', 'judge', 'self_execute')"
        )

    # ── Table 1: orchestrator_runs ───────────────────────────────────────────
    if not _table_exists("orchestrator_runs"):
        op.create_table(
            "orchestrator_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("goal", sa.Text, nullable=True),
            sa.Column("target_url", sa.Text, nullable=True),
            sa.Column(
                "artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "credential_profile_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("environment", sa.String(200), nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "planning", "running", "passed", "failed",
                    "partial", "error", "cancelled",
                    name="orchestrator_run_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "ai_test_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_test_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "visual_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("visual_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("self_execute_answer", sa.Text, nullable=True),
            sa.Column("summary", sa.Text, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("started_at", sa.DateTime, nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("ix_orchestrator_runs_project_id", "orchestrator_runs", ["project_id"])
        op.create_index("ix_orchestrator_runs_artifact_id", "orchestrator_runs", ["artifact_id"])
        op.create_index("ix_orchestrator_runs_ai_test_run_id", "orchestrator_runs", ["ai_test_run_id"])
        op.create_index("ix_orchestrator_runs_visual_run_id", "orchestrator_runs", ["visual_run_id"])

    # ── Table 2: orchestrator_step_decisions ─────────────────────────────────
    if not _table_exists("orchestrator_step_decisions"):
        op.create_table(
            "orchestrator_step_decisions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orchestrator_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "step",
                postgresql.ENUM(
                    "hands", "judge", "self_execute",
                    name="orchestrator_step_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("invoked", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("model_provider", sa.String(50), nullable=True),
            sa.Column("model_name", sa.String(200), nullable=True),
            sa.Column("is_deterministic", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("rationale", sa.Text, nullable=False),
            sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_orchestrator_step_decisions_run_id", "orchestrator_step_decisions", ["run_id"]
        )


def downgrade() -> None:
    op.drop_table("orchestrator_step_decisions")
    op.drop_table("orchestrator_runs")
    for enum_name in (
        "orchestrator_step_enum",
        "orchestrator_run_status_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
