"""SOW: review flags on skills, partial-extraction status, source structure

Four independent additions, all additive and nullable so nothing in flight
at deploy time changes behaviour:

1. ai_skills.review_status / review_reason
   A requirement the source document describes but does not specify well
   enough to write executable steps for used to be DROPPED outright by
   design_ingest._validate_checkpoint -- it returned None, the checkpoint
   never existed, and no skill was created. An omitted requirement is
   invisible: nobody reviews it, nobody clarifies it, and the SOW silently
   reads as fully covered. These columns let such a requirement be captured
   and FLAGGED instead ("needs_review" / "needs_design_flow"), so it shows
   up as work to be clarified rather than as nothing at all.

   Deliberately a plain VARCHAR, not a Postgres enum: 0036 is the precedent
   for how one-way an enum addition is (Postgres has no ALTER TYPE ... DROP
   VALUE), and this vocabulary is expected to grow.

2. sow_source_status_enum += 'done_with_errors'
   Ledger extraction over a chunked document no longer discards every
   chunk's facts when one chunk fails. Saving partial results is only
   acceptable if a partial result can never be MISTAKEN for a complete one,
   so the partial case gets its own terminal status rather than reusing
   'done'. Guarded ADD VALUE, same pattern as 0036/0008.

3. sow_requirements_ledger.source_heading_path (JSONB)
   The heading path the fact physically sat under in the imported document.
   Written by the chunker, never by the model, so it cannot be hallucinated.
   This is what lets a regenerated SOW mirror the source document's own
   section order instead of an order the grouping model invented.

4. sow_document_sources.source_outline (JSONB)
   The imported document's table of contents as the chunker saw it, in
   original order -- the document-level counterpart of (3).

5. sow_documents.pending_section_keys / pending_new_fact_count
   Which already-drafted sections a newly attached source affects. Attaching
   a transcript to a generated SOW previously left the user two bad options:
   regenerate everything (losing hand edits, re-paying for every section) or
   guess which sections to rewrite from a checkbox list. These columns hold
   the computed answer so the Rewrite dialog can pre-tick exactly the
   affected sections. Advisory only -- nothing redrafts off them.

Revision ID: 0040_sow_needs_review
Revises: 0039_sow_source_progress
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_sow_needs_review"
down_revision: Union[str, None] = "0039_sow_source_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum_value_exists(enum_name: str, value: str) -> bool:
    """Copied verbatim from 0036_ai_run_needs_review_status.py -- the
    established pattern in this repo for a re-runnable enum-value addition."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = :enum_name AND e.enumlabel = :value"
        ),
        {"enum_name": enum_name, "value": value},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # 1 — skill review flags
    op.add_column(
        "ai_skills",
        sa.Column("review_status", sa.String(length=30), nullable=True),
    )
    op.add_column("ai_skills", sa.Column("review_reason", sa.Text(), nullable=True))
    # Partial index: the overwhelming majority of skills are fully specified
    # and store NULL here, and the only query that matters is "show me the
    # ones that need attention".
    op.create_index(
        "ix_ai_skills_review_status",
        "ai_skills",
        ["review_status"],
        unique=False,
        postgresql_where=sa.text("review_status IS NOT NULL"),
    )

    # 2 — partial-extraction terminal status.
    # No explicit transaction wrapping, matching 0036/0008, which already run
    # cleanly in this project's Alembic setup.
    if not _enum_value_exists("sow_source_status_enum", "done_with_errors"):
        op.execute(
            "ALTER TYPE sow_source_status_enum ADD VALUE IF NOT EXISTS 'done_with_errors'"
        )

    # 3 + 4 — imported-document structure
    op.add_column(
        "sow_requirements_ledger",
        sa.Column("source_heading_path", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "sow_document_sources",
        sa.Column("source_outline", postgresql.JSONB(), nullable=True),
    )

    # 5 — affected-section hints
    op.add_column(
        "sow_documents",
        sa.Column("pending_section_keys", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "sow_documents",
        sa.Column("pending_new_fact_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sow_documents", "pending_new_fact_count")
    op.drop_column("sow_documents", "pending_section_keys")
    op.drop_column("sow_document_sources", "source_outline")
    op.drop_column("sow_requirements_ledger", "source_heading_path")
    op.drop_index("ix_ai_skills_review_status", table_name="ai_skills")
    op.drop_column("ai_skills", "review_reason")
    op.drop_column("ai_skills", "review_status")
    # 'done_with_errors' is intentionally left on sow_source_status_enum:
    # Postgres has no ALTER TYPE ... DROP VALUE, and any source row already
    # carrying it would need an explicit status reassignment this migration
    # cannot decide on the DBA's behalf. Same convention as 0036/0008.
