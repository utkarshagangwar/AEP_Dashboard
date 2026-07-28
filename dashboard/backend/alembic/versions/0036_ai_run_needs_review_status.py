"""New Vibe Test Phase 4 -- add 'needs_review' to ai_run_status_enum.

GEval now gates a functional/goal-based run's displayed status
(_persist_result in app/workers/tasks/ai_execution.py): if the agent
self-reports "passed" but the independent DeepEval quality score comes back
below VIBE_TEST_EVAL_THRESHOLD, the run is set to this new status instead of
silently showing "passed" -- see app/models/ai_runs.py's AIRunStatus and
app/services/ai_eval.py.

Same guarded-ADD-VALUE pattern as 0008_add_celery_task_id_and_queued_status.py
(the only prior precedent in this repo for adding a Postgres enum value):
IF NOT EXISTS makes this safe to re-run, and no explicit transaction wrapping
is used, matching that migration exactly since it already runs cleanly in
this project's Alembic setup.

Revision ID: 0036_needs_review
Revises: 0035_functional_test_viewport
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_needs_review"
down_revision: Union[str, None] = "0035_functional_test_viewport"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if not _enum_value_exists("ai_run_status_enum", "needs_review"):
        op.execute("ALTER TYPE ai_run_status_enum ADD VALUE IF NOT EXISTS 'needs_review'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- removing an enum label
    # requires rebuilding the type (rename old, create new, migrate every
    # column/dependent view, drop old) and is intentionally not attempted
    # here, same convention as 0008's downgrade leaving its added enum value
    # in place. Any row already showing needs_review would also need a
    # explicit status reassignment first, which this migration cannot decide
    # on the DBA's behalf.
    pass
