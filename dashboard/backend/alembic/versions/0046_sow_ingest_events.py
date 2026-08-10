"""Live extraction progress — one row per step that actually ran.

Backs app/services/sow_progress.py and the Skills & TDDs progress panel.

Append-only history, written by the code doing the work rather than derived
from SowPart rows afterwards. A derived status could only ever show a fixed
list of phases in a fixed order, which would claim steps that did not run
(zoning with TDD_ZONING=0) and stay silent on the ones that did (gap repair,
the variant cap, the cross-part merge).

Rows are intentionally kept when an ingest fails: "extraction started, then
errored" is the state a reader most needs, and deleting the evidence would
blank the panel at the one moment it matters.

Revision ID: 0046_sow_ingest_events
Revises: 0045_project_ui_inventory
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_sow_ingest_events"
down_revision: Union[str, None] = "0045_project_ui_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sow_ingest_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_number", sa.Integer(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    # The only query this table serves: every event for one artifact, in
    # order. Composite rather than a plain artifact_id index so the poll —
    # which runs every couple of seconds while an ingest is live — is an
    # index scan with no sort.
    op.create_index(
        "ix_sow_ingest_events_artifact_seq",
        "sow_ingest_events",
        ["artifact_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sow_ingest_events_artifact_seq", table_name="sow_ingest_events"
    )
    op.drop_table("sow_ingest_events")
