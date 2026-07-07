"""Add missing indexes on test_runs.status and defects.status/severity

These columns are filtered/sorted on in reports.py, executions.py, and
defects.py (WHERE tr.status = ..., ORDER BY CASE d.severity ..., etc.) but
had no index — harmless at today's row counts, but each one will start
costing a sequential scan as test_runs/defects grow.

Revision ID: 0018_status_indexes
Revises: 0017_user_permissions
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_status_indexes"
down_revision: Union[str, None] = "0017_user_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE tablename = :table AND indexname = :index_name"
        ),
        {"table": table, "index_name": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _index_exists("test_runs", "ix_test_runs_status"):
        op.create_index("ix_test_runs_status", "test_runs", ["status"], unique=False)

    if not _index_exists("defects", "ix_defects_status"):
        op.create_index("ix_defects_status", "defects", ["status"], unique=False)

    if not _index_exists("defects", "ix_defects_severity"):
        op.create_index("ix_defects_severity", "defects", ["severity"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_defects_severity", table_name="defects")
    op.drop_index("ix_defects_status", table_name="defects")
    op.drop_index("ix_test_runs_status", table_name="test_runs")
