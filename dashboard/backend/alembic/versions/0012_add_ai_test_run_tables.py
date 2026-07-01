"""Add AI test run tables: ai_credential_profiles, ai_test_runs, ai_run_events

Revision ID: 0012_ai_test_runs
Revises: 0011_hybrid_ai
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_ai_test_runs"
down_revision: Union[str, None] = "0011_hybrid_ai"
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
    # ── Enums (raw SQL so SQLAlchemy never auto-emits CREATE TYPE on table create)
    if not _enum_exists("ai_run_status_enum"):
        op.execute(
            "CREATE TYPE ai_run_status_enum AS ENUM "
            "('pending', 'running', 'passed', 'failed', 'inconclusive', 'cancelled')"
        )
    if not _enum_exists("ai_event_status_enum"):
        op.execute(
            "CREATE TYPE ai_event_status_enum AS ENUM "
            "('pending', 'running', 'passed', 'failed')"
        )
    if not _enum_exists("ai_step_type_enum"):
        op.execute(
            "CREATE TYPE ai_step_type_enum AS ENUM "
            "('deterministic', 'ai_scoped')"
        )

    # ── Table 1: ai_credential_profiles ──────────────────────────────────────
    if not _table_exists("ai_credential_profiles"):
        op.create_table(
            "ai_credential_profiles",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("allowed_domains", postgresql.JSONB, nullable=True),
            sa.Column("credentials_json", sa.Text, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_ai_credential_profiles_project_id",
            "ai_credential_profiles",
            ["project_id"],
        )

    # ── Table 2: ai_test_runs ─────────────────────────────────────────────────
    if not _table_exists("ai_test_runs"):
        op.create_table(
            "ai_test_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("goal", sa.Text, nullable=False),
            sa.Column("environment", sa.String(200), nullable=True),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "credential_profile_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("credential_profile_name", sa.String(200), nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "running", "passed", "failed",
                    "inconclusive", "cancelled",
                    name="ai_run_status_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("started_at", sa.DateTime, nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column("step_count", sa.Integer, server_default="0"),
            sa.Column("summary", sa.Text, nullable=True),
            sa.Column("failing_step_index", sa.Integer, nullable=True),
            sa.Column("failing_step_description", sa.Text, nullable=True),
            sa.Column("failing_step_screenshot_url", sa.Text, nullable=True),
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
        op.create_index("ix_ai_test_runs_project_id", "ai_test_runs", ["project_id"])
        op.create_index("ix_ai_test_runs_created_by", "ai_test_runs", ["created_by"])
        op.create_index("ix_ai_test_runs_status", "ai_test_runs", ["status"])

    # ── Table 3: ai_run_events ────────────────────────────────────────────────
    if not _table_exists("ai_run_events"):
        op.create_table(
            "ai_run_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_test_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer, nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "running", "passed", "failed",
                    name="ai_event_status_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column(
                "step_type",
                postgresql.ENUM(
                    "deterministic", "ai_scoped",
                    name="ai_step_type_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="deterministic",
            ),
            sa.Column("elapsed_ms", sa.Integer, nullable=True),
            sa.Column("screenshot_url", sa.Text, nullable=True),
            sa.Column("highlighted_element", postgresql.JSONB, nullable=True),
            sa.Column("is_failing_step", sa.Boolean, server_default="false"),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("ix_ai_run_events_run_id", "ai_run_events", ["run_id"])
        op.create_index(
            "ix_ai_run_events_run_seq",
            "ai_run_events",
            ["run_id", "sequence"],
        )


def downgrade() -> None:
    if _table_exists("ai_run_events"):
        op.drop_table("ai_run_events")
    if _table_exists("ai_test_runs"):
        op.drop_table("ai_test_runs")
    if _table_exists("ai_credential_profiles"):
        op.drop_table("ai_credential_profiles")

    for enum_name in ("ai_step_type_enum", "ai_event_status_enum", "ai_run_status_enum"):
        if _enum_exists(enum_name):
            op.execute(f"DROP TYPE {enum_name}")
