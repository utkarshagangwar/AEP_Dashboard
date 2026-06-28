"""add total_tests column to test_runs

Revision ID: 0009_total_tests
Revises: 0008_celery_queued
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_total_tests"
down_revision: Union[str, None] = "0008_celery_queued"
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


def upgrade() -> None:
    if not _column_exists("test_runs", "total_tests"):
        op.add_column(
            "test_runs",
            sa.Column("total_tests", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("test_runs", "total_tests"):
        op.drop_column("test_runs", "total_tests")
