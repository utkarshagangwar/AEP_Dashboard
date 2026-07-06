"""Add ai_skills table and skill/summary columns on ai_test_runs

Revision ID: 0016_ai_skills
Revises: 0015_orchestrator
Create Date: 2026-07-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_ai_skills"
down_revision: Union[str, None] = "0015_orchestrator"
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


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("ai_skills"):
        op.create_table(
            "ai_skills",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("goal_hash", sa.String(64), nullable=False),
            sa.Column(
                "source_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_test_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("environment", sa.String(200), nullable=True),
            sa.Column(
                "credential_profile_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("history_json", sa.Text(), nullable=False),
            sa.Column("step_count", sa.Integer(), server_default="0"),
            sa.Column(
                "times_replayed", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("last_replay_status", sa.String(20), nullable=True),
            sa.Column("last_replayed_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_ai_skills_goal_hash", "ai_skills", ["goal_hash"], unique=True
        )
        op.create_index("ix_ai_skills_project_id", "ai_skills", ["project_id"])

    if not _column_exists("ai_test_runs", "raw_summary"):
        op.add_column("ai_test_runs", sa.Column("raw_summary", sa.Text(), nullable=True))
    if not _column_exists("ai_test_runs", "run_type"):
        op.add_column(
            "ai_test_runs",
            sa.Column(
                "run_type", sa.String(20), nullable=False, server_default="ai"
            ),
        )
    if not _column_exists("ai_test_runs", "skill_id"):
        op.add_column(
            "ai_test_runs",
            sa.Column(
                "skill_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_skills.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _column_exists("ai_test_runs", "skill_id"):
        op.drop_column("ai_test_runs", "skill_id")
    if _column_exists("ai_test_runs", "run_type"):
        op.drop_column("ai_test_runs", "run_type")
    if _column_exists("ai_test_runs", "raw_summary"):
        op.drop_column("ai_test_runs", "raw_summary")
    if _table_exists("ai_skills"):
        op.drop_table("ai_skills")
