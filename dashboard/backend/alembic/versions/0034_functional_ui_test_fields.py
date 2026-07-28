"""Structured Functional Test fields + UI Test linked requirement.

New Vibe Test creation is split into two dedicated flows: UI Test (backed
by the existing visual_judge.judge() / visual_runs pipeline) and Functional
Test (backed by the Hands browser agent / ai_test_runs pipeline). This
migration is purely additive -- no existing column is modified or dropped.

ai_test_runs gets the structured Functional Test fields that replace a
single free-text goal as the *authored* input (the compiled goal text is
still generated server-side and stored in the existing `goal` column, so
every downstream consumer of `goal` -- ai_runner.py, ai_eval.py, the
Results tab, Skill auto-save -- needs zero changes):
  test_category      'functional' for rows created via the new structured
                      flow; null for every pre-existing row and for rows
                      still created via a plain goal (Android, skill
                      replay, Autonomous QA orchestrator) -- those paths
                      are unmodified by this feature.
  preconditions       free-text setup assumed true before step 1.
  steps               JSONB ordered list of atomic step strings.
  expected_results    JSONB list of expected-result strings (soft
                      assertions folded into the compiled goal).
  test_data           JSONB list of {name, values} named data sets --
                      lets one test case run parameterized across several
                      inputs (one ai_test_runs row per data set).
  test_type           'happy' | 'negative' | 'edge'.
  linked_requirement  free-text requirement/checkpoint reference, shared
                      field name with visual_runs.linked_requirement so
                      coverage can eventually be reported across both test
                      types from one column shape.

visual_runs gets the same linked_requirement column for the UI Test flow.

Revision ID: 0034_functional_ui_test_fields
Revises: 0033_ai_run_eval
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "0034_functional_ui_test_fields"
down_revision: Union[str, None] = "0033_ai_run_eval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_test_runs", sa.Column("test_category", sa.String(length=20), nullable=True)
    )
    op.add_column("ai_test_runs", sa.Column("preconditions", sa.Text(), nullable=True))
    op.add_column("ai_test_runs", sa.Column("steps", JSONB(), nullable=True))
    op.add_column("ai_test_runs", sa.Column("expected_results", JSONB(), nullable=True))
    op.add_column("ai_test_runs", sa.Column("test_data", JSONB(), nullable=True))
    op.add_column(
        "ai_test_runs", sa.Column("test_type", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "ai_test_runs", sa.Column("linked_requirement", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "visual_runs", sa.Column("linked_requirement", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("visual_runs", "linked_requirement")
    op.drop_column("ai_test_runs", "linked_requirement")
    op.drop_column("ai_test_runs", "test_type")
    op.drop_column("ai_test_runs", "test_data")
    op.drop_column("ai_test_runs", "expected_results")
    op.drop_column("ai_test_runs", "steps")
    op.drop_column("ai_test_runs", "preconditions")
    op.drop_column("ai_test_runs", "test_category")
