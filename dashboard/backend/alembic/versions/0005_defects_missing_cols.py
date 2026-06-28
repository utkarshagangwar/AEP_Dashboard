"""add project_id and reported_by to defects

Revision ID: 0005_defects_cols
Revises: 0004_suite_cols
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_defects_cols"
down_revision: Union[str, None] = "0004_suite_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone()
    return result is not None


def upgrade() -> None:
    if not _column_exists("defects", "project_id"):
        op.add_column(
            "defects",
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_defects_project_id",
            "defects", "projects",
            ["project_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_defects_project_id", "defects", ["project_id"], unique=False
        )

    if not _column_exists("defects", "reported_by"):
        op.add_column(
            "defects",
            sa.Column(
                "reported_by",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_defects_reported_by",
            "defects", "users",
            ["reported_by"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_defects_reported_by", "defects", ["reported_by"], unique=False
        )


def downgrade() -> None:
    op.drop_index("ix_defects_reported_by", table_name="defects")
    op.drop_constraint("fk_defects_reported_by", "defects", type_="foreignkey")
    op.drop_column("defects", "reported_by")
    op.drop_index("ix_defects_project_id", table_name="defects")
    op.drop_constraint("fk_defects_project_id", "defects", type_="foreignkey")
    op.drop_column("defects", "project_id")
