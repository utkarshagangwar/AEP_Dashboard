"""SOW chunking Phase 3 -- sow_parts chunk provenance

SOW_CHUNKING_PLAN.md §2.7. Adds the structural context that
app.services.doc_chunking now produces for every part: which section it
came from, where in the source it sits, which strategy split it, and the
exact framing text sent to the LLM alongside it.

All four columns are nullable with NO backfill, and the migration is purely
additive:
  * sow_parts rows are created once at ingest and never re-chunked, so rows
    written by the previous character-window splitter keep working -- they
    simply carry NULLs. Re-ingesting an artifact is the migration path.
  * A NULL `strategy` therefore means "chunked before this feature existed",
    which is distinguishable from every real strategy value.

`strategy` is the one column with behavioural weight: the value
"hard_split" marks a part that had to be cut at an arbitrary point because a
single unit (an unbroken paragraph, an enormous table row) exceeded the
character budget on its own. It is surfaced in the UI so that degradation is
assertable from a vibe test instead of living only in worker logs.

Revision ID: 0038_chunk_context
Revises: 0037_ai_run_visual_eval
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038_chunk_context"
down_revision: Union[str, None] = "0037_ai_run_visual_eval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Section breadcrumb, e.g. ["2. Functional Requirements", "2.1 Candidate List"].
    op.add_column("sow_parts", sa.Column("heading_path", JSONB(), nullable=True))
    # "p.12" / "§4.3.2" / "00:14:32" -- traceability back into the source.
    op.add_column("sow_parts", sa.Column("locator", sa.String(length=200), nullable=True))
    # Which strategy produced this part; "hard_split" is the degradation signal.
    op.add_column("sow_parts", sa.Column("strategy", sa.String(length=40), nullable=True))
    # The exact framing block sent to the LLM -- reproducibility for debugging.
    op.add_column("sow_parts", sa.Column("context_header", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sow_parts", "context_header")
    op.drop_column("sow_parts", "strategy")
    op.drop_column("sow_parts", "locator")
    op.drop_column("sow_parts", "heading_path")
