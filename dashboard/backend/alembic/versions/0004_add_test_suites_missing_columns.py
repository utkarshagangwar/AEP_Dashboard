"""add missing columns to test_suites and defects

Revision ID: 0004_suite_cols
Revises: 0003_add_audit_log_updated_at
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_suite_cols"
down_revision: Union[str, None] = "0003_add_audit_log_updated_at"
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
    # --- test_suites: add suite_type and is_active ---
    suite_type_enum = sa.Enum(
        "smoke", "regression", "sanity", "exploratory", "full",
        name="suite_type_enum",
    )
    suite_type_enum.create(op.get_bind(), checkfirst=True)

    if not _column_exists("test_suites", "suite_type"):
        op.add_column(
            "test_suites",
            sa.Column("suite_type", suite_type_enum, nullable=True),
        )

    if not _column_exists("test_suites", "is_active"):
        op.add_column(
            "test_suites",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )

    # --- defects: add project_id and reported_by ---
    if not _column_exists("defects", "project_id"):
        op.add_column(
            "defects",
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
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
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_defects_reported_by", "defects", ["reported_by"], unique=False
        )


def downgrade() -> None:
    op.drop_index("ix_defects_reported_by", table_name="defects")
    op.drop_column("defects", "reported_by")
    op.drop_index("ix_defects_project_id", table_name="defects")
    op.drop_column("defects", "project_id")
    op.drop_column("test_suites", "is_active")
    op.drop_column("test_suites", "suite_type")
    sa.Enum(name="suite_type_enum").drop(op.get_bind(), checkfirst=True)
