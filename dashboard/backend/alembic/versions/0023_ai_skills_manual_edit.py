"""Add manually_edited flag to ai_skills (view/edit from the Skills tab)

A human can now edit a skill's name/goal/project by hand. manually_edited
protects that edit from being silently overwritten the next time its source
SOW/video part is re-analyzed (see app.services.skill_store).

Revision ID: 0023_ai_skills_manual_edit
Revises: 0022_ai_skills_prompt_source
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_ai_skills_manual_edit"
down_revision: Union[str, None] = "0022_ai_skills_prompt_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("ai_skills", "manually_edited"):
        op.add_column(
            "ai_skills",
            sa.Column(
                "manually_edited", sa.Boolean(), nullable=False, server_default="false"
            ),
        )


def downgrade() -> None:
    if _column_exists("ai_skills", "manually_edited"):
        op.drop_column("ai_skills", "manually_edited")
