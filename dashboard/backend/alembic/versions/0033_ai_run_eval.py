"""Vibe Testing quality score -- ai_test_runs.eval_score/eval_reason/eval_status/eval_metric

Post-run DeepEval (GEval) scoring of how well a finished New Vibe Test /
Skill Replay run actually accomplished its stated goal -- see
app/services/ai_eval.py. All columns nullable and purely additive; a null
eval_status means either the run predates this feature, wasn't eligible
(Android platform, Autonomous QA, or a non-terminal/inconclusive/cancelled
status -- see _persist_result's gating in
app/workers/tasks/ai_execution.py), or scoring itself failed for any
reason (deepeval unavailable, every LLM in the router chain failed, etc.)
-- that failure path is swallowed by design (app/services/ai_eval.py
never raises) so it can never fail run persistence.

Revision ID: 0033_ai_run_eval
Revises: 0032_ai_run_video
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_ai_run_eval"
down_revision: Union[str, None] = "0032_ai_run_video"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_test_runs", sa.Column("eval_score", sa.Float(), nullable=True))
    op.add_column("ai_test_runs", sa.Column("eval_reason", sa.Text(), nullable=True))
    op.add_column(
        "ai_test_runs", sa.Column("eval_status", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "ai_test_runs", sa.Column("eval_metric", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_test_runs", "eval_metric")
    op.drop_column("ai_test_runs", "eval_status")
    op.drop_column("ai_test_runs", "eval_reason")
    op.drop_column("ai_test_runs", "eval_score")
