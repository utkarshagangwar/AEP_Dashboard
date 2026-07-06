"""Add parse_status/parse_error to design_artifacts (SOW ingestion, Phase 3)

Revision ID: 0014_artifact_parse_status
Revises: 0013_visual_qa
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_artifact_parse_status"
down_revision: Union[str, None] = "0013_visual_qa"
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


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _enum_exists("parse_status_enum"):
        op.execute(
            "CREATE TYPE parse_status_enum AS ENUM "
            "('not_required', 'pending', 'processing', 'done', 'error')"
        )
    if not _column_exists("design_artifacts", "parse_status"):
        op.add_column(
            "design_artifacts",
            sa.Column(
                "parse_status",
                postgresql.ENUM(
                    "not_required", "pending", "processing", "done", "error",
                    name="parse_status_enum", create_type=False,
                ),
                nullable=False,
                # Existing rows are figma_png references — nothing to parse.
                server_default="not_required",
            ),
        )
    if not _column_exists("design_artifacts", "parse_error"):
        op.add_column(
            "design_artifacts", sa.Column("parse_error", sa.Text, nullable=True)
        )


def downgrade() -> None:
    op.drop_column("design_artifacts", "parse_error")
    op.drop_column("design_artifacts", "parse_status")
    op.execute("DROP TYPE IF EXISTS parse_status_enum")
