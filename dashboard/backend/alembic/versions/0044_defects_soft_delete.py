"""Defects: soft delete with an attributed deleter.

Adds to `defects`:
  deleted_at  when the bug was removed from the list (NULL = live)
  deleted_by  which user removed it, FK users(id) ON DELETE SET NULL

Soft rather than hard, matching the SOW document delete: a bug record is the
audit trail for a failure, so "delete" hides it from the working list and an
admin or QA lead can put it back. Nothing is cascaded — the row and every
attachment to it stay exactly as they were.

`deleted_by` exists because "recoverable" is only useful if you can also see
who removed it; the list surfaces it as "Deleted by <name>" on the Deleted
filter. ON DELETE SET NULL rather than CASCADE, because deleting a user must
never take bug history with it — the row falls back to "Deleted by —".

The partial index carries the default read path. Every non-Deleted query adds
`deleted_at IS NULL`, which is true for essentially the whole table, so a plain
index on the column would be dead weight; the partial index instead covers the
Deleted view, which is the selective one.

Revision ID: 0044_defects_soft_delete
Revises: 0043_tdd_extraction_fields
Create Date: 2026-08-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_defects_soft_delete"
down_revision: Union[str, None] = "0043_tdd_extraction_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "defects",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column("deleted_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_defects_deleted_by_users",
        "defects",
        "users",
        ["deleted_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_defects_deleted_at",
        "defects",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_defects_deleted_at", table_name="defects")
    op.drop_constraint("fk_defects_deleted_by_users", "defects", type_="foreignkey")
    op.drop_column("defects", "deleted_by")
    op.drop_column("defects", "deleted_at")
