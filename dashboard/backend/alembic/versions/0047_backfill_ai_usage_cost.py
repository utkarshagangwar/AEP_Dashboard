"""Backfill cost_usd for AI usage events logged before the AXON cost bug fix.

litellm.completion_cost()'s standalone prompt_tokens=/completion_tokens=
keyword form was removed from the installed litellm version (1.74.x) — every
call to app.services.ai_usage.estimate_cost_usd() therefore raised a
TypeError that its own broad except silently swallowed into "no cost
available", for every event on that code path. That path is every Hands-run
event (ai_runner.py's Google and AXON logging both call estimate_cost_usd()
directly, never litellm.completion_cost(completion_response=...) — only the
llm_router.py/Judge/Brain/SOW path used that still-working form), which is
why AXON — the default Hands provider — priced at $0.00 for every call ever
logged while Google's Judge/Brain/SOW cost kept showing up normally.

See app.services.ai_usage.estimate_cost_usd's updated docstring for the fix
itself (switched to litellm.cost_per_token()). This migration does not
change any live code path — it only recomputes cost_usd, once, for rows
that already recorded prompt_tokens/completion_tokens but landed with a
NULL cost_usd because of the bug, using the now-fixed estimate_cost_usd().
A row litellm still has no pricing data for (unrecognized model id) is left
NULL, exactly as it would be if logged fresh today — this never fabricates
a number the live code path couldn't itself have produced.

Revision ID: 0047_backfill_ai_usage_cost
Revises: 0046_sow_ingest_events
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_backfill_ai_usage_cost"
down_revision: Union[str, None] = "0046_sow_ingest_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Imported here rather than duplicating litellm's model-id mapping
    # inline — this is the one function that decides "what does this call
    # cost", and it must stay the single source of truth for that, backfill
    # included. estimate_cost_usd() never raises (see its own docstring).
    from app.services.ai_usage import estimate_cost_usd

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, provider, model, prompt_tokens, completion_tokens
            FROM ai_usage_events
            WHERE cost_usd IS NULL
              AND (prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL)
            """
        )
    ).fetchall()

    priced = 0
    for row in rows:
        cost = estimate_cost_usd(row.provider, row.model, row.prompt_tokens, row.completion_tokens)
        if cost is None:
            continue
        bind.execute(
            sa.text("UPDATE ai_usage_events SET cost_usd = :cost WHERE id = :row_id"),
            {"cost": cost, "row_id": row.id},
        )
        priced += 1
    print(f"ai_usage cost backfill: priced {priced}/{len(rows)} historical event(s)")


def downgrade() -> None:
    # Not meaningfully reversible — the original NULLs were a bug, not real
    # state worth restoring, so downgrade is intentionally a no-op.
    pass
