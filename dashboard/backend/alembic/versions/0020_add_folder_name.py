"""Add folder_name to projects and test_suites

Suite discovery previously matched automation folders to rows by their
user-editable `name`, so renaming a project or suite made the next scan
blind to it and register a fresh duplicate. `folder_name` is a separate,
immutable key discovery uses instead — backfilled here from the current
`name` since for un-renamed rows the two are still identical.

Revision ID: 0020_folder_name
Revises: 0019_project_environments
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_folder_name"
down_revision: Union[str, None] = "0019_project_environments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("folder_name", sa.String(255), nullable=True))
    op.add_column("test_suites", sa.Column("folder_name", sa.String(255), nullable=True))

    op.execute("UPDATE projects SET folder_name = name WHERE folder_name IS NULL")
    op.execute("UPDATE test_suites SET folder_name = name WHERE folder_name IS NULL")


def downgrade() -> None:
    op.drop_column("test_suites", "folder_name")
    op.drop_column("projects", "folder_name")
