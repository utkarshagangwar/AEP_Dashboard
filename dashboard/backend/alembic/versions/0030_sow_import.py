"""Import SOW (SOW tab) — new artifact type

Extends the existing `artifact_type_enum` (design_artifacts) with one new
source type: 'sow_import' -- an uploaded pre-existing SOW/requirements
document (.docx/.pdf/.txt/.md) attached to a sow_documents row as a source,
parsed into the requirements ledger via
app/services/sow_import.py + app/services/sow_ledger.py's
extract_ledger_from_sow_document*, same mechanism 0028 already established
for 'meeting_transcript'/'meeting_recording'. No new columns/tables --
reuses design_artifacts and sow_document_sources exactly as-is.

Deliberately a distinct value from the pre-existing 'sow' artifact type,
which belongs to the separate, unmodified SOW-Checkpoints/Vibe-Testing
pipeline (app/services/design_ingest.py) -- see app/models/visual_qa.py's
ArtifactType.sow_import docstring for why the two must not be conflated.

Revision ID: 0030_sow_import
Revises: 0029_sow_document_sources
Create Date: 2026-07-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_sow_import"
down_revision: Union[str, None] = "0029_sow_document_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helper (matches 0028's established convention) ──────────────

def _enum_value_exists(enum_name: str, value: str) -> bool:
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
    if not _enum_value_exists("artifact_type_enum", "sow_import"):
        op.execute("ALTER TYPE artifact_type_enum ADD VALUE IF NOT EXISTS 'sow_import'")


def downgrade() -> None:
    # Postgres does not support dropping individual enum values -- same
    # precedent already established in 0028's downgrade() for
    # 'meeting_transcript'/'meeting_recording': the added value is left in
    # place rather than attempting a full enum rebuild.
    pass
