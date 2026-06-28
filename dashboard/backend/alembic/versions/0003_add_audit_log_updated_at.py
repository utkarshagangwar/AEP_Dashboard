"""add updated_at to audit_logs

Revision ID: 0003_add_audit_log_updated_at
Revises: 0002_add_project_tables
Create Date: 2026-06-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_audit_log_updated_at"
down_revision: Union[str, None] = "0002_add_project_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # updated_at already exists in 0001_initial_auth_schema; this migration
    # was created by mistake and is kept as a no-op to preserve the revision chain.
    pass


def downgrade() -> None:
    pass
