"""Add sales/ba/hr roles and a permissions column to users

Revision ID: 0017_user_permissions
Revises: 0016_ai_skills
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_user_permissions"
down_revision: Union[str, None] = "0016_ai_skills"
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
    # New department roles — role is now a descriptive label only and
    # carries no implicit access (see app/core/permissions.py). Existing
    # admin/qa_lead/qa_engineer/developer/viewer roles are unaffected.
    for value in ("sales", "ba", "hr"):
        if not _enum_value_exists("user_role", value):
            op.execute(f"ALTER TYPE user_role ADD VALUE IF NOT EXISTS '{value}'")

    # Explicit per-user feature access, granted by an admin at creation or
    # edit time (see app/core/permissions.py for the list of valid keys).
    # Empty by default for everyone, including existing users — they need
    # to be re-granted access manually via the Users page after this ships.
    if not _column_exists("users", "permissions"):
        op.add_column(
            "users",
            sa.Column(
                "permissions",
                postgresql.JSONB,
                nullable=False,
                server_default="[]",
            ),
        )


def downgrade() -> None:
    if _column_exists("users", "permissions"):
        op.drop_column("users", "permissions")
    # Postgres doesn't support removing individual enum values; leaving
    # sales/ba/hr in place on downgrade matches this repo's existing
    # pattern for additive enum migrations (e.g. 0008's 'queued' status).
