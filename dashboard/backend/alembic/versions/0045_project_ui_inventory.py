"""Project UI label inventory — what a project's screens/controls are CALLED.

Backs app/services/ui_inventory.py. One row per project, built from that
project's uploaded evidence (figma_png artifacts + labels already recovered
from digested walkthrough videos) and handed to the SOW extraction prompt so
generated tests name real buttons instead of the document's wording.

Every column is derived and rebuildable from the artifacts, so there is
nothing to backfill and nothing is lost by dropping the table: a project with
no row simply extracts exactly as it did before, with no visual context. That
is also why there is no NOT NULL on the payload columns — a build that failed
still records WHY in build_error rather than leaving no trace.

Revision ID: 0045_project_ui_inventory
Revises: 0044_defects_soft_delete
Create Date: 2026-08-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_project_ui_inventory"
down_revision: Union[str, None] = "0044_defects_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_ui_inventory",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inventory_json", postgresql.JSONB(), nullable=True),
        sa.Column("rendered_text", sa.Text(), nullable=True),
        sa.Column("source_artifact_ids", postgresql.JSONB(), nullable=True),
        sa.Column("screen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("built_by_model", sa.String(length=200), nullable=True),
        sa.Column("build_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    # Unique, not merely indexed: one inventory per project means the
    # extraction path never has to choose between two versions, and a
    # concurrent rebuild collides instead of quietly creating a second row.
    op.create_index(
        "ix_project_ui_inventory_project_id",
        "project_ui_inventory",
        ["project_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_ui_inventory_project_id", table_name="project_ui_inventory"
    )
    op.drop_table("project_ui_inventory")
