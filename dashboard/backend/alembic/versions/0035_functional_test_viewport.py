"""Functional Test viewport preset -- ai_test_runs.viewport_preset

New Vibe Test Phase 2 (execution reliability hardening): lets a Functional
Test deliberately exercise a mobile/tablet responsive breakpoint instead of
always running at the fixed desktop viewport. See
app/services/ai_runner.py's VIEWPORT_PRESETS for the "desktop" | "tablet" |
"mobile" -> {width, height} mapping. Null (every pre-existing row, and every
non-Functional-Test run) means "desktop" -- today's exact fixed behavior.

Revision ID: 0035_functional_test_viewport
Revises: 0034_functional_ui_test_fields
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_functional_test_viewport"
down_revision: Union[str, None] = "0034_functional_ui_test_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_test_runs", sa.Column("viewport_preset", sa.String(length=20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_test_runs", "viewport_preset")
