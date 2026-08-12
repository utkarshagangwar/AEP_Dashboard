"""Project Intelligence — AI Context Feedback Loop (Phase 4, spec §20).

build_project_brief() assembles a compact, token-budgeted text brief from
already-verified Project Intelligence knowledge — screens, navigation
flow, proven locators, unresolved drift flags, and stale areas — for
injection into an AI test run's goal, just before the agent starts.

INJECTION POINT (spec's corrected v2.0 -> v3.0 §0.1 item 4, and §20 itself):
workers/tasks/ai_execution.run_ai_test_task, AFTER _resolve_run_inputs()
resolves the run's environment/credentials and BEFORE run_ai_test_sync()
is called. NEVER app/services/start_context.py — that module resolves
start URL and credential identity only, is deliberately secret-free, and
is called synchronously by api/v1/ai_runs.py at run-submit time to fail
fast; assembling DB-backed knowledge there would put this work on the
synchronous API request path for every submission. This file does not
import or modify start_context.py.

FAIL-OPEN, same posture as every other Phase 1-3 addition. Every public
function here is wrapped so that any error — a bad query, a missing
project, a malformed flow model — is caught, logged, and degrades to
"nothing extra", never raised. A run whose context brief could not be
built proceeds with its original, unmodified goal, exactly as it does
today. This feature must never be able to block or slow a run.

ONLY VERIFIED KNOWLEDGE REACHES THE AGENT — with one named exception.
PiScreen / PiComponent / PiFlow rows are only ever read here at
status='verified'; pending, unreviewed intelligence never reaches an
agent. The one deliberate exception is PiDriftFlag: an *unresolved*
(status='pending') drift flag is itself the useful signal ("this locator
broke", "this control was renamed") and withholding it until a human
reviews it would defeat the entire point of surfacing it pre-emptively.
This reading follows spec §20's own listed brief contents, which names
"unresolved drift flags" as one of the five categories — but it is an
interpretation, not a literal instruction, and is flagged here the same
way Phase 3's report flagged its SSO/allowed_domains interpretation.

LOCATOR SELF-HEALING (spec §20: "pi_components becomes the canonical
fallback source for the existing AI locator recovery"). This file does
NOT write to pi_components.success_count/fail_count — that bookkeeping
already exists and already runs on every re-observation
(services/pi_extract.py, since Phase 1/2; see its "reobservation" comment
docstring). This file only READS it, surfacing the components with the
strongest success/fail record as "proven locator" guidance text in the
brief. There is no separate, deterministic per-element locator-resolution
hook in this codebase to wire into instead: browser-use's Agent resolves
elements from its own live DOM/vision snapshot on every step (see
ai_runner.py — `agent.run()` and `agent.rerun_history()` are both opaque,
internal-to-the-library element resolution; this codebase does not
intercept element lookup at that level for a normal run OR a skill
replay). Textual guidance placed in the agent's own task/goal is
therefore the actual, honest integration point available for "canonical
fallback source" — not a new selector-injection layer this file invents.

MEASUREMENT (spec §20: "steps, tokens and cost per run are already
recorded in ai_usage. Phase 4 delivers a before/after comparison, not a
claim"). log_context_injection() stamps a lightweight marker row into the
EXISTING ai_usage_events table (source="pi_context") every time a brief is
actually injected, reusing ai_usage.log_usage_event()'s current public
signature completely unchanged — no edit to ai_usage.py, no migration.
context_effectiveness_report() then compares runs with that marker against
runs without it. See its own docstring for the one approximation it makes
(step count).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.logging import get_logger
from app.services import pi_ingest

logger = get_logger(__name__)

# Rough token estimate (4 chars/token) — no tokenizer dependency added just
# for a soft budget check; consistent with this codebase's other
# char-based size estimates (see e.g. ui_inventory.py's prompt trimming).
_CHARS_PER_TOKEN = 4

_MAX_SCREENS = 20
_MAX_COMPONENTS = 25
_MAX_DRIFT_FLAGS = 8
_MAX_STALE_SCREENS = 10
_STALE_AFTER_DAYS = 14


def resolve_environment_id(db, *, project_id, environment_label: Optional[str]):
    """Best-effort (project_id, environment label) -> ProjectEnvironment.id
    lookup, exact case-insensitive match only. Returns None on no label, no
    match, or any error.

    Deliberately simpler than start_context.py's own private environment
    lookup (which additionally falls back to a project's single configured
    environment when the label is missing, because a run there must
    resolve to *some* address or fail fast). This is advisory only —
    build_project_brief() already degrades cleanly to a project-wide brief
    (via pi_flow.get_verified_model()'s own environment_id=None fallback)
    when this returns None, so there is no fail-fast case to replicate
    here, and no dependency is taken on that other module's private
    helper.
    """
    if not project_id or not environment_label:
        return None
    try:
        from app.models.project import ProjectEnvironment

        wanted = environment_label.strip().lower()
        if not wanted:
            return None
        rows = (
            db.query(ProjectEnvironment)
            .filter(ProjectEnvironment.project_id == project_id)
            .all()
        )
        for row in rows:
            if (row.environment or "").strip().lower() == wanted:
                return row.id
        return None
    except Exception:
        logger.warning(
            "pi_context: environment lookup failed for project %s", project_id, exc_info=True,
        )
        return None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def build_project_brief(
    db, *, project_id, environment_id=None, budget_tokens: Optional[int] = None,
) -> Optional[str]:
    """Returns a compact text brief, or None when PI_CONTEXT_ENABLED is
    off, project_id is missing, nothing has been verified yet for this
    project, or the build fails for any reason. An empty brief is worse
    than none — it would add prompt noise for zero benefit — so an empty
    result is always None, never an empty string.
    """
    if project_id is None or not pi_ingest.context_enabled():
        return None

    budget_tokens = budget_tokens or pi_ingest.context_budget_tokens()
    timeout_s = pi_ingest.context_build_timeout_s()
    start = time.monotonic()

    def _out_of_time() -> bool:
        return (time.monotonic() - start) > timeout_s

    try:
        from sqlalchemy import case

        from app.models.project_intelligence import (
            PiComponent,
            PiDriftFlag,
            PiScreen,
            PiStatus,
        )
        from app.services import pi_flow
        from app.services.flow_validation import render_flow_reference

        blocks: list[str] = []

        # ── 1. Verified screens with descriptions, recently changed first ──
        screens_base = db.query(PiScreen).filter(
            PiScreen.project_id == project_id, PiScreen.status == PiStatus.verified,
        )
        if environment_id is not None:
            screens_base = screens_base.filter(PiScreen.environment_id == environment_id)
        screens = screens_base.order_by(PiScreen.updated_at.desc()).limit(_MAX_SCREENS).all()

        if screens:
            lines = ["KNOWN SCREENS (verified):"]
            for s in screens:
                desc = (s.description or s.title or "").strip()
                lines.append(f"  - {s.route}" + (f" — {desc}" if desc else ""))
            blocks.append("\n".join(lines))

        # ── 2. Verified navigation flow — reuses the exact same renderer
        #      the extraction system prompt already uses (pi_flow.
        #      get_verified_model + flow_validation.render_flow_reference),
        #      not reinvented here. ──
        if not _out_of_time():
            flow_model = pi_flow.get_verified_model(db, project_id, environment_id)
            nav_text = render_flow_reference(flow_model) if flow_model else ""
            if nav_text.strip():
                blocks.append("KNOWN NAVIGATION FLOW (verified):\n" + nav_text.strip())

        # ── 3. Proven locators — verified components, success-ranked ──
        if not _out_of_time():
            components_q = (
                db.query(PiComponent)
                .join(PiScreen, PiComponent.screen_id == PiScreen.id)
                .filter(
                    PiComponent.project_id == project_id,
                    PiComponent.status == PiStatus.verified,
                    PiComponent.locator.isnot(None),
                )
            )
            if environment_id is not None:
                components_q = components_q.filter(PiScreen.environment_id == environment_id)
            components = (
                components_q.order_by(
                    PiComponent.success_count.desc(), PiComponent.fail_count.asc(),
                )
                .limit(_MAX_COMPONENTS)
                .all()
            )
            # Only surface a locator with a net-positive track record — a
            # component with more failures than successes is exactly what
            # a locator_broken drift flag exists to catch (see block 4
            # below), not something to hand the agent as "proven".
            proven = [c for c in components if (c.success_count or 0) > (c.fail_count or 0)]
            if proven:
                lines = [
                    "PROVEN LOCATORS (controls with a working track record on this "
                    "project — prefer these when interacting with a matching control, "
                    "but always verify against what the live page actually shows "
                    "before acting; do not trust blindly):"
                ]
                for c in proven:
                    lines.append(
                        f"  - {c.component_type} \"{c.label}\": {c.locator} "
                        f"(worked {c.success_count}x, failed {c.fail_count}x)"
                    )
                blocks.append("\n".join(lines))

        # ── 4. Unresolved drift flags — see module docstring for why these
        #      are surfaced despite being status='pending' ──
        if not _out_of_time():
            drift_q = db.query(PiDriftFlag).filter(
                PiDriftFlag.project_id == project_id, PiDriftFlag.status == PiStatus.pending,
            )
            if environment_id is not None:
                drift_q = drift_q.join(PiScreen, PiDriftFlag.screen_id == PiScreen.id).filter(
                    PiScreen.environment_id == environment_id
                )
            drift_flags = (
                drift_q.order_by(
                    case(
                        (PiDriftFlag.severity == "high", 0),
                        (PiDriftFlag.severity == "medium", 1),
                        else_=2,
                    ),
                    PiDriftFlag.created_at.desc(),
                )
                .limit(_MAX_DRIFT_FLAGS)
                .all()
            )
            if drift_flags:
                lines = [
                    "UNRESOLVED CHANGES (detected automatically, pending human review — "
                    "treat with caution, may no longer match the live page):"
                ]
                for f in drift_flags:
                    lines.append(f"  - [{f.severity}] {f.drift_type.value}: {f.description}")
                blocks.append("\n".join(lines))

        # ── 5. Stale areas — verified screens not recently re-observed ──
        if not _out_of_time():
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_AFTER_DAYS)
            stale = (
                screens_base.filter(PiScreen.last_seen_at < stale_cutoff.replace(tzinfo=None))
                .order_by(PiScreen.last_seen_at.asc())
                .limit(_MAX_STALE_SCREENS)
                .all()
            )
            if stale:
                lines = [
                    f"NOT RECENTLY OBSERVED (not visited by any capture in "
                    f"{_STALE_AFTER_DAYS}+ days — treat with extra care, may have changed):"
                ]
                for s in stale:
                    lines.append(f"  - {s.route}")
                blocks.append("\n".join(lines))

        if not blocks:
            return None

        header = (
            "PROJECT INTELLIGENCE BRIEF (auto-generated from verified prior observations "
            "on this project — use it to move faster, but always verify against what the "
            "live page actually shows; this is guidance, not a substitute for looking):"
        )

        def _assemble(kept_blocks: list[str]) -> str:
            return header + "\n\n" + "\n\n".join(kept_blocks)

        # Hard token budget, truncation by priority (spec §20). Blocks were
        # appended above in priority order, so dropping from the end drops
        # the lowest-priority section first.
        text = _assemble(blocks)
        while blocks and estimate_tokens(text) > budget_tokens:
            blocks.pop()
            text = _assemble(blocks) if blocks else ""

        if not text:
            return None

        # Final char-level trim for the rare case where even the single
        # highest-priority block alone exceeds budget.
        if estimate_tokens(text) > budget_tokens:
            max_chars = budget_tokens * _CHARS_PER_TOKEN
            text = text[:max_chars].rsplit("\n", 1)[0] + "\n  ... (truncated)"

        return text
    except Exception:
        logger.warning(
            "pi_context: build_project_brief failed for project %s — run proceeds "
            "without injected context", project_id, exc_info=True,
        )
        return None


def log_context_injection(*, run_id, brief_tokens: int) -> None:
    """Marks a run as having received an injected context brief, via a
    lightweight ai_usage_events row (source="pi_context"). Reuses
    ai_usage.log_usage_event()'s existing public signature completely
    unchanged — no edit to ai_usage.py, no migration — piggybacking on the
    same table (and the same Admin > AI Usage dashboard) every other AI
    cost signal already lands in. prompt_tokens/total_tokens carry the
    brief's own estimated size so this row is informative standing alone,
    not just a boolean marker.

    Best-effort: log_usage_event() itself never raises (it swallows and
    logs internally), and this wrapper adds its own try/except on top
    purely as defense in depth — a failure to log this marker must never
    affect the run it describes.
    """
    try:
        from app.services import ai_usage

        ai_usage.log_usage_event(
            source="pi_context",
            provider="pi",
            model="context_brief",
            status="ok",
            prompt_tokens=brief_tokens,
            total_tokens=brief_tokens,
            run_type="ai_run",
            run_id=str(run_id),
        )
    except Exception:
        logger.warning(
            "pi_context: failed to log context-injection marker for run %s",
            run_id, exc_info=True,
        )


_EMPTY_GROUP_STATS = {"run_count": 0, "avg_steps": 0.0, "avg_tokens": 0.0, "avg_cost_usd": 0.0}


def _empty_effectiveness_report() -> dict:
    return {
        "with_context": dict(_EMPTY_GROUP_STATS),
        "without_context": dict(_EMPTY_GROUP_STATS),
        "note": "No AI run usage data yet.",
    }


def context_effectiveness_report(db, *, project_id=None, sample_limit: int = 5000) -> dict:
    """Before/after comparison, not a claim (spec §20: "Measurement ships
    with the phase ... Phase 4 delivers a before/after comparison, not a
    claim"). Compares average tokens / cost / step-count of AI runs that
    received an injected context brief (log_context_injection() above)
    against runs that did not, using data already recorded in
    ai_usage_events — no new table, no new column.

    APPROXIMATION, stated plainly: step count is the number of source=
    "hands" usage events recorded for a run_id, since that is the only
    per-run granularity this codebase's usage log already provides — there
    is no separate, cheaper "steps" counter to join against instead. This
    typically tracks actual agent steps closely (each browser-use planning
    call is one LLM call) but is not asserted as exact.

    Read-only, best-effort: returns the zeroed/empty shape (never raises)
    on any query failure, consistent with every other PI reporting path
    (pi_flow.get_verified_model, etc.).
    """
    try:
        from app.models.ai_usage import AIUsageEvent

        hands_q = db.query(AIUsageEvent).filter(
            AIUsageEvent.source == "hands",
            AIUsageEvent.run_type == "ai_run",
            AIUsageEvent.run_id.isnot(None),
        )

        if project_id is not None:
            from app.models.ai_runs import AITestRun

            run_ids = [
                str(r[0])
                for r in db.query(AITestRun.id).filter(AITestRun.project_id == project_id).all()
            ]
            if not run_ids:
                return _empty_effectiveness_report()
            hands_q = hands_q.filter(AIUsageEvent.run_id.in_(run_ids))

        hands_events = (
            hands_q.order_by(AIUsageEvent.created_at.desc()).limit(sample_limit).all()
        )
        if not hands_events:
            return _empty_effectiveness_report()

        context_run_ids = {
            r[0]
            for r in db.query(AIUsageEvent.run_id)
            .filter(AIUsageEvent.source == "pi_context", AIUsageEvent.run_id.isnot(None))
            .all()
        }

        by_run: dict[str, list] = {}
        for ev in hands_events:
            by_run.setdefault(ev.run_id, []).append(ev)

        def _summarize(run_ids_subset: list[str]) -> dict:
            if not run_ids_subset:
                return dict(_EMPTY_GROUP_STATS)
            step_counts, token_totals, cost_totals = [], [], []
            for rid in run_ids_subset:
                evs = by_run[rid]
                step_counts.append(len(evs))
                token_totals.append(sum(e.total_tokens or 0 for e in evs))
                cost_totals.append(float(sum(e.cost_usd or 0 for e in evs)))
            n = len(run_ids_subset)
            return {
                "run_count": n,
                "avg_steps": round(sum(step_counts) / n, 1),
                "avg_tokens": round(sum(token_totals) / n, 1),
                "avg_cost_usd": round(sum(cost_totals) / n, 4),
            }

        with_context = [rid for rid in by_run if rid in context_run_ids]
        without_context = [rid for rid in by_run if rid not in context_run_ids]

        return {
            "with_context": _summarize(with_context),
            "without_context": _summarize(without_context),
            "note": (
                "Step count is approximated as the number of LLM ('hands') calls "
                "recorded per run; tokens/cost are exact figures from ai_usage_events. "
                f"Sampled from the {len(hands_events)} most recent matching usage events."
            ),
        }
    except Exception:
        logger.warning("pi_context: effectiveness report failed", exc_info=True)
        return _empty_effectiveness_report()
