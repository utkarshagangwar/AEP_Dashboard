"""Add environments column to projects

Adds a nullable text-array column so each project can declare which
environments (dev/staging/production/...) it exercises, editable from the
project detail page.

Revision ID: 0019_project_environments
Revises: 0018_status_indexes
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_project_environments"
down_revision: Union[str, None] = "0018_status_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "environments",
            postgresql.ARRAY(sa.String()),
            nullable=True,
            server_default=sa.text("'{dev,staging,production}'"),
        ),
    )
    op.alter_column("projects", "environments", server_default=None)


def downgrade() -> None:
    op.drop_column("projects", "environments")
