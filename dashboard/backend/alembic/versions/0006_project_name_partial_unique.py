"""replace project name unique index with partial unique index on active rows

Revision ID: 0006_project_name_partial
Revises: 0005_defects_cols
Create Date: 2026-06-26

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0006_project_name_partial"
down_revision: Union[str, None] = "0005_defects_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_projects_name", table_name="projects")
    op.create_index(
        "ix_projects_name_active",
        "projects",
        ["name"],
        unique=True,
        postgresql_where=text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_projects_name_active", table_name="projects")
    op.create_index("ix_projects_name", "projects", ["name"], unique=True)
