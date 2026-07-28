"""AI Test Run video recording — ai_test_runs.video_path

New Vibe Test / Skill Replay runs now record a full-session video (see
app/services/ai_run_capture.py) instead of per-step screenshots. This column
stores the server-side path to the finished mp4 on the shared visual_qa_data
volume; null means no video (legacy run, or capture failed/never enabled —
Autonomous QA's Hands sub-step and Android runs never set this).

Purely additive — no existing column or table touched.

Revision ID: 0032_ai_run_video
Revises: 0031_ai_usage
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_ai_run_video"
down_revision: Union[str, None] = "0031_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_test_runs", sa.Column("video_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_test_runs", "video_path")
