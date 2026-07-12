"""Support prompt-only skills on ai_skills (no recorded actions required)

SOW/video checkpoint parsing now saves a detailed instruction directly to
ai_skills as soon as parsing finishes, instead of requiring a live browser
run first. Such a row has no recorded action history until someone actually
runs it and it passes, so history_json must become nullable. source_type/
source_artifact_id trace a skill back to the document it came from;
source_key gives prompt skills a stable upsert identity independent of
goal_hash (whose content is expected to change across re-analyses).

Revision ID: 0022_ai_skills_prompt_source
Revises: 0021_sow_parts
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_ai_skills_prompt_source"
down_revision: Union[str, None] = "0021_sow_parts"
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
    op.alter_column("ai_skills", "history_json", existing_type=sa.Text(), nullable=True)

    if not _column_exists("ai_skills", "source_type"):
        op.add_column("ai_skills", sa.Column("source_type", sa.String(20), nullable=True))
    if not _column_exists("ai_skills", "source_artifact_id"):
        op.add_column(
            "ai_skills",
            sa.Column(
                "source_artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not _column_exists("ai_skills", "source_key"):
        op.add_column("ai_skills", sa.Column("source_key", sa.String(300), nullable=True))
        op.create_index(
            "ix_ai_skills_source_key", "ai_skills", ["source_key"], unique=True
        )


def downgrade() -> None:
    if _column_exists("ai_skills", "source_key"):
        op.drop_index("ix_ai_skills_source_key", table_name="ai_skills")
        op.drop_column("ai_skills", "source_key")
    if _column_exists("ai_skills", "source_artifact_id"):
        op.drop_column("ai_skills", "source_artifact_id")
    if _column_exists("ai_skills", "source_type"):
        op.drop_column("ai_skills", "source_type")
    op.execute("DELETE FROM ai_skills WHERE history_json IS NULL")
    op.alter_column("ai_skills", "history_json", existing_type=sa.Text(), nullable=False)
