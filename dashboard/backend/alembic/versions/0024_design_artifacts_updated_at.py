"""Add updated_at to design_artifacts (staleness detection for stuck ingestion)

design_artifacts had no updated_at column, unlike every other status-bearing
table (sow_parts, ai_test_runs, ai_skills, ...) — needed so
visual_qa_reconcile can tell a row that's genuinely still 'processing' apart
from one whose worker died mid-flight and will never finish on its own.

Revision ID: 0024_design_artifacts_updated_at
Revises: 0023_ai_skills_manual_edit
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_design_artifacts_updated_at"
down_revision: Union[str, None] = "0023_ai_skills_manual_edit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if not _column_exists("design_artifacts", "updated_at"):
        op.add_column(
            "design_artifacts",
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    if _column_exists("design_artifacts", "updated_at"):
        op.drop_column("design_artifacts", "updated_at")
