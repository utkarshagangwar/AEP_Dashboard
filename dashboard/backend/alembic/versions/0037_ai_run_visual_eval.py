"""New Vibe Test Phase 4 -- ai_test_runs.visual_eval_score/reason/status/metric

Second, complementary judge pass: app.services.ai_eval.evaluate_expected_results()
compares a run's final-state screenshot against its own `expected_results`
(Functional Test only, see 0034_functional_ui_test_fields), independent of
the existing action-trace eval_score/eval_reason (0033_ai_run_eval). All
columns nullable and purely additive -- a null visual_eval_status means the
run predates this feature, isn't a Functional Test with expected_results,
had no final screenshot available, or the vision pass itself failed for any
reason (never blocks run persistence -- see ai_eval.evaluate_expected_results).

Revision ID: 0037_ai_run_visual_eval
Revises: 0036_needs_review
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_ai_run_visual_eval"
down_revision: Union[str, None] = "0036_needs_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_test_runs", sa.Column("visual_eval_score", sa.Float(), nullable=True))
    op.add_column("ai_test_runs", sa.Column("visual_eval_reason", sa.Text(), nullable=True))
    op.add_column(
        "ai_test_runs", sa.Column("visual_eval_status", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "ai_test_runs", sa.Column("visual_eval_metric", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_test_runs", "visual_eval_metric")
    op.drop_column("ai_test_runs", "visual_eval_status")
    op.drop_column("ai_test_runs", "visual_eval_reason")
    op.drop_column("ai_test_runs", "visual_eval_score")
