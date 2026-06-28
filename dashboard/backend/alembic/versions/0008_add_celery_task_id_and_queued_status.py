"""add celery_task_id column and queued status to test_runs

Revision ID: 0008_celery_queued
Revises: 0007_project_product_nullable
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_celery_queued"
down_revision: Union[str, None] = "0007_product_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def _enum_value_exists(enum_name: str, value: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = :enum_name AND e.enumlabel = :value"
        ),
        {"enum_name": enum_name, "value": value},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # Add 'queued' to run_status enum if missing
    if not _enum_value_exists("run_status", "queued"):
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'queued'")

    # Add celery_task_id column if missing
    if not _column_exists("test_runs", "celery_task_id"):
        op.add_column(
            "test_runs",
            sa.Column("celery_task_id", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("test_runs", "celery_task_id"):
        op.drop_column("test_runs", "celery_task_id")
