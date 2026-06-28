"""add source_suite and tags columns to test_results

Revision ID: 0010_result_details
Revises: 0009_total_tests
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_result_details"
down_revision: Union[str, None] = "0009_total_tests"
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
    if not _column_exists("test_results", "source_suite"):
        op.add_column(
            "test_results",
            sa.Column("source_suite", sa.String(500), nullable=True),
        )
    if not _column_exists("test_results", "tags"):
        op.add_column(
            "test_results",
            sa.Column("tags", sa.String(1000), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("test_results", "tags"):
        op.drop_column("test_results", "tags")
    if _column_exists("test_results", "source_suite"):
        op.drop_column("test_results", "source_suite")
