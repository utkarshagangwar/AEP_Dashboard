"""Add platform_name to design_artifacts (mandatory context for video ingestion)

Video walkthrough analysis has no way to know what product it's looking at,
so when the uploaded recording is ambiguous (or, per an observed incident,
happens to show unrelated on-screen text like another document), the model
has nothing to anchor on. platform_name is a user-declared, mandatory-for-
video field passed into the Gemini prompt so the model is told what it's
watching instead of inferring/assuming it. Nullable at the DB level (SOW/
figma_png rows never set it) — mandatory-ness is enforced at the API layer
for video uploads only.

Revision ID: 0025_add_video_platform_name
Revises: 0024_design_artifacts_updated_at
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_add_video_platform_name"
down_revision: Union[str, None] = "0024_design_artifacts_updated_at"
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
    if not _column_exists("design_artifacts", "platform_name"):
        op.add_column(
            "design_artifacts",
            sa.Column("platform_name", sa.String(length=300), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("design_artifacts", "platform_name"):
        op.drop_column("design_artifacts", "platform_name")
