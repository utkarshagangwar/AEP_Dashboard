"""SOW attached-source extraction progress

The Attached sources table could only ever render a static "Processing"
badge, because SowDocumentSource carried nothing but a coarse status. On a
large chunked document that badge sits unchanged for minutes, which reads as
"the worker is dead" rather than "part 5 of 12".

Three additive, nullable columns fix that:

  * progress_stage   -- short machine token for the current phase
                        ("reading", "chunking", "extracting", "saving").
                        NULL means "no progress ever reported", which is
                        exactly the state of every row written before this
                        migration and of any source whose worker never got
                        far enough to report -- the UI falls back to the old
                        plain badge for those, so nothing regresses.
  * progress_current  -- units finished within the current stage.
  * progress_total    -- total units in the current stage. NULL (or <= 1)
                        means the work is a single indivisible call (a
                        meeting recording, a design image), where a
                        percentage would be fabricated. The UI shows a stage
                        label instead of a bar for those -- deliberately no
                        fake 0->99 timer.

Nullable with no backfill and no server_default: in-flight sources at deploy
time keep working, they just render the way they always did until their next
extraction run.

Revision ID: 0039_sow_source_progress
Revises: 0038_chunk_context
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_sow_source_progress"
down_revision: Union[str, None] = "0038_chunk_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sow_document_sources",
        sa.Column("progress_stage", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "sow_document_sources",
        sa.Column("progress_current", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sow_document_sources",
        sa.Column("progress_total", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sow_document_sources", "progress_total")
    op.drop_column("sow_document_sources", "progress_current")
    op.drop_column("sow_document_sources", "progress_stage")
