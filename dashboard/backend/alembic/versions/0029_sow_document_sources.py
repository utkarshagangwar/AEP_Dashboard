"""SOW Creation & Rewrite — Phase 1: sow_document_sources

Phase 0 shipped sow_requirements_ledger (document_id + source_artifact_id
per extracted FACT) but nothing represented "this design_artifact is
attached to this document as input material, and here's its extraction
status" prior to any facts existing -- there was no way to list what's
attached to a document, show per-source ingest progress, or detach a
source. This table is that missing join.

sow_document_sources
  One row per (document, artifact) attachment. artifact_id points at the
  existing design_artifacts table (now also used for the two source types
  added in migration 0028: meeting_transcript, meeting_recording, plus the
  pre-existing figma_png for design references). status/error_message
  track this document's ledger-extraction run for that source
  independently of design_artifacts.parse_status, which belongs to a
  different pipeline (SOW Checkpoints/Video Walkthrough) and must not be
  conflated with this one even when the same artifact row is reused.

Revision ID: 0029_sow_document_sources
Revises: 0028_sow_foundation
Create Date: 2026-07-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_sow_document_sources"
down_revision: Union[str, None] = "0028_sow_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _enum_exists("sow_source_status_enum"):
        op.execute(
            "CREATE TYPE sow_source_status_enum AS ENUM "
            "('pending', 'processing', 'done', 'error')"
        )

    if not _table_exists("sow_document_sources"):
        op.create_table(
            "sow_document_sources",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "processing", "done", "error",
                    name="sow_source_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("error_message", sa.Text, nullable=True),
            # Informational only -- how many ledger rows this source's most
            # recent extraction produced. Not authoritative (query
            # sow_requirements_ledger directly for that); just avoids a
            # join for the sources list UI's summary column.
            sa.Column("ledger_fact_count", sa.Integer, nullable=True),
            sa.Column(
                "added_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            # Bumped on every status transition -- what sow_reconcile.py's
            # stale-source sweep keys off, same convention as
            # sow_generation_jobs.updated_at (plan §11.2).
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(),
                onupdate=sa.func.now(), nullable=False,
            ),
        )
        op.create_index(
            "ix_sow_document_sources_document_id", "sow_document_sources", ["document_id"]
        )
        op.create_index(
            "ix_sow_document_sources_artifact_id", "sow_document_sources", ["artifact_id"]
        )
        op.create_index(
            "ix_sow_document_sources_status", "sow_document_sources", ["status"]
        )
        op.create_index(
            "ix_sow_document_sources_doc_artifact_unique",
            "sow_document_sources",
            ["document_id", "artifact_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_table("sow_document_sources")
    op.execute("DROP TYPE IF EXISTS sow_source_status_enum")
