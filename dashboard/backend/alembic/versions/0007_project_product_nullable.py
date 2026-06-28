"""make project.product column nullable to match model

Revision ID: 0007_product_nullable
Revises: 0006_project_name_partial
Create Date: 2026-06-26

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_product_nullable"
down_revision: Union[str, None] = "0006_project_name_partial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("projects", "product", nullable=True)


def downgrade() -> None:
    op.alter_column("projects", "product", nullable=False)
