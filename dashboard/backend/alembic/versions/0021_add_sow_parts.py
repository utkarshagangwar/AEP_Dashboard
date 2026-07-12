"""Add sow_parts table + total_parts on design_artifacts (SOW chunking, Phase 3)

Large SOW documents used to be silently truncated at 60k chars before ever
reaching the LLM. This migration adds the storage needed to instead split a
large document into parts, analyzed one at a time: sow_parts holds each
chunk's text/status/checkpoints, and design_artifacts.total_parts records how
many parts a document was split into (always 1 for documents that fit in a
single part, and for non-SOW artifact types).

Revision ID: 0021_sow_parts
Revises: 0020_folder_name
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_sow_parts"
down_revision: Union[str, None] = "0020_folder_name"
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


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("design_artifacts", "total_parts"):
        op.add_column(
            "design_artifacts",
            sa.Column("total_parts", sa.Integer, nullable=False, server_default="1"),
        )

    if not _table_exists("sow_parts"):
        op.create_table(
            "sow_parts",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("part_number", sa.Integer, nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("char_count", sa.Integer, nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "not_required", "pending", "processing", "done", "error",
                    name="parse_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("checkpoints", postgresql.JSONB, nullable=True),
            sa.Column("parsed_by_model", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_sow_parts_artifact_id", "sow_parts", ["artifact_id"])
        op.create_index(
            "ix_sow_parts_artifact_part_unique",
            "sow_parts",
            ["artifact_id", "part_number"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_sow_parts_artifact_part_unique", table_name="sow_parts")
    op.drop_index("ix_sow_parts_artifact_id", table_name="sow_parts")
    op.drop_table("sow_parts")
    op.drop_column("design_artifacts", "total_parts")
