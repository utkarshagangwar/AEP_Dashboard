"""Project Intelligence — Ingestion layer.

Normalises raw observations (a Robot Framework suite's keyword-level
capture, or a Vibe/AI run's action history) into the common shape
`pi_extract.process_normalized_observations` consumes, and writes the
pi_capture_events row that is the replay/debug audit trail for all of it.

Never does LLM work — that is pi_extract's job. Never raises past its
public functions: every path here is auxiliary to a test run or a review
surface and must degrade to "nothing captured" rather than take anything
else down with it (same rule rf_listener.py and ui_inventory.py already
follow).

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §14.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Feature flags (opt-out convention: unset means enabled, matching the
#    TDD_* / SOW_ENABLED precedents already in this codebase — except the
#    master switch, which is the one kill switch that must default OFF
#    until Phase 1 sign-off, per spec §26.2) ──────────────────────────────

def pi_enabled() -> bool:
    """Master switch. Off -> every Project Intelligence code path (worker
    hooks, API, the flow_validation seam) returns/no-ops immediately."""
    return os.environ.get("PI_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _opt_out_flag(name: str) -> bool:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return True
    return raw.lower() not in ("0", "false", "no", "off")


def rf_capture_enabled() -> bool:
    return pi_enabled() and _opt_out_flag("PI_CAPTURE_RF")


def vibe_capture_enabled() -> bool:
    return pi_enabled() and _opt_out_flag("PI_CAPTURE_VIBE")


def heal_ledger_enabled() -> bool:
    """PI_HEAL_LEDGER — the Phase 2 ledger-write kill switch (spec §19.4 /
    table 16: "off until Phase 2 is signed off"). Deliberately NOT the
    opt-out convention: detection and classification (pi_drift_flags rows)
    happen and are shown regardless of this flag; only the apply operation
    that writes to sow_requirements_ledger is gated by it. Off -> the API
    layer 403s the apply endpoint (see api/v1/project_intelligence.py)."""
    return pi_enabled() and os.environ.get("PI_HEAL_LEDGER", "").strip().lower() in (
        "1", "true", "yes",
    )


_DEFAULT_REMOVED_THRESHOLD = 3
_DEFAULT_RENAME_SIMILARITY_THRESHOLD = 0.93


def removed_threshold() -> int:
    """PI_REMOVED_THRESHOLD — consecutive screen re-visits a component must
    be absent from before it is classified `removed` (spec table 10:
    "N is configurable; default 3, so one flaky run cannot delete a
    control"). Falls back to the default on anything unset/unparseable/
    less than 1 — never lets a misconfigured value make removal
    hair-trigger."""
    raw = os.environ.get("PI_REMOVED_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_REMOVED_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_REMOVED_THRESHOLD
    return value if value >= 1 else _DEFAULT_REMOVED_THRESHOLD


def crawl_enabled() -> bool:
    """PI_CRAWL_ENABLED — the Phase 3 scheduled-crawler kill switch (spec
    table 16: "off until Phase 3"). Deliberately the master-switch
    convention (unset/anything-but-1/true/yes means OFF), not the opt-out
    convention every PI_CAPTURE_* flag uses — this one spends money and
    drives an unattended browser against a real environment, the same
    posture as PI_HEAL_LEDGER. This is the GLOBAL gate; a project must
    ALSO have Project.pi_crawl_enabled set (spec §28 Q2 — "off everywhere
    by default, opt-in per project") before any of its environments are
    ever crawled — see workers/tasks/pi_crawl.py."""
    return pi_enabled() and os.environ.get("PI_CRAWL_ENABLED", "").strip().lower() in (
        "1", "true", "yes",
    )


def design_extraction_enabled() -> bool:
    """PI_CAPTURE_DESIGN_PATTERNS — opt-out convention (matches
    PI_CAPTURE_RF/PI_CAPTURE_VIBE), since this is a bounded, capped-count
    vision pass (PI_CRAWL_MAX_SCREENSHOTS) that only ever runs as part of
    an already-gated crawl (crawl_enabled() above), not an independent
    spend surface someone could accidentally enable on its own."""
    return pi_enabled() and _opt_out_flag("PI_CAPTURE_DESIGN_PATTERNS")


_DEFAULT_ARTIFACT_RETENTION_DAYS = 90
_DEFAULT_CRAWL_MAX_STEPS = 40
_DEFAULT_CRAWL_MAX_DURATION_S = 1200
_DEFAULT_CRAWL_MAX_SCREENSHOTS = 5
_DEFAULT_CRAWL_STAGGER_S = 300


def _positive_int_env(name: str, default: int) -> int:
    """Shared by every PI_CRAWL_* numeric setting below — never lets a
    misconfigured value (unset, unparseable, zero, negative) make a
    scheduled/unattended feature behave unpredictably; falls back to the
    documented default instead."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def artifact_retention_days() -> int:
    """PI_ARTIFACT_RETENTION_DAYS — spec table 16 default 90 (table 18 Q7,
    confirmed at Phase 3 sign-off). How long a crawl screenshot file stays
    on VISUAL_DATA_DIR before workers/tasks/pi_crawl.cleanup_expired_
    artifacts deletes it. The pi_design_patterns KNOWLEDGE row it produced
    is not deleted by this — only its evidence_ref pointer is cleared once
    the file it points to is gone (spec §16: "Screenshots are referenced,
    never duplicated" — the pointer, not the fact, is what expires)."""
    return _positive_int_env("PI_ARTIFACT_RETENTION_DAYS", _DEFAULT_ARTIFACT_RETENTION_DAYS)


def crawl_max_steps() -> int:
    """PI_CRAWL_MAX_STEPS — the hard step ceiling spec §14.3 requires
    ("a hard step ceiling, and a wall-clock timeout"), passed straight
    through to ai_runner.run_ai_test_sync(max_steps=...). Deliberately a
    much smaller default than New Vibe Test's own 100-step default: a
    crawl runs unattended, nightly, and per-environment, so its per-run
    cost needs a tighter budget than a human-initiated single test."""
    return _positive_int_env("PI_CRAWL_MAX_STEPS", _DEFAULT_CRAWL_MAX_STEPS)


def crawl_max_duration_s() -> int:
    """PI_CRAWL_MAX_DURATION_S — the wall-clock backstop spec §14.3
    requires, passed to ai_runner.run_ai_test_sync(max_duration_s=...).
    Default 1200s (20 minutes) — bounded enough that a staggered nightly
    run across several projects/environments (see
    workers/celery_app.py's beat_schedule) cannot pile up unboundedly."""
    return _positive_int_env("PI_CRAWL_MAX_DURATION_S", _DEFAULT_CRAWL_MAX_DURATION_S)


def crawl_max_screenshots() -> int:
    """PI_CRAWL_MAX_SCREENSHOTS — caps how many of a crawl's step
    screenshots are ever sent to a vision-tier LLM call (one call per
    screenshot — see services/pi_crawl.py). The dominant cost lever named
    in spec §10/table 4 ("Flash tier by default; vision tier only where
    required") for this phase specifically; kept low by default since this
    runs nightly and repeatedly, not once per project like ui_inventory's
    vocabulary pass."""
    return _positive_int_env("PI_CRAWL_MAX_SCREENSHOTS", _DEFAULT_CRAWL_MAX_SCREENSHOTS)


def crawl_stagger_s() -> int:
    """PI_CRAWL_STAGGER_S — spacing (via Celery's apply_async(countdown=))
    between each eligible (project, environment) pair's crawl start (spec
    table 13: "Celery Beat, per project, staggered, default nightly") —
    so a night with several eligible projects does not launch every
    Chromium instance at once."""
    return _positive_int_env("PI_CRAWL_STAGGER_S", _DEFAULT_CRAWL_STAGGER_S)


def context_enabled() -> bool:
    """PI_CONTEXT_ENABLED — the Phase 4 AI-context-feedback-loop kill
    switch (spec table 16: "off until Phase 4"). Master-switch convention
    (unset/anything-but-1/true/yes means OFF), matching PI_CRAWL_ENABLED
    and PI_HEAL_LEDGER, not the opt-out convention every PI_CAPTURE_* flag
    uses: this one changes what every AI test run's agent actually sees
    (its goal text is augmented with a generated brief before the run
    starts — see services/pi_context.py), so it stays a deliberate admin
    opt-in even once this code ships, the same posture as the other two
    Phase-gated switches before it."""
    return pi_enabled() and os.environ.get("PI_CONTEXT_ENABLED", "").strip().lower() in (
        "1", "true", "yes",
    )


_DEFAULT_CONTEXT_BUDGET_TOKENS = 1200
_DEFAULT_CONTEXT_BUILD_TIMEOUT_S = 5


def context_budget_tokens() -> int:
    """PI_CONTEXT_BUDGET_TOKENS — the hard token budget spec §20 requires
    ("Hard token budget with truncation by priority") for one injected
    brief. Kept small relative to a run's own overall prompt budget — this
    is meant to orient the agent with a handful of known facts, not
    replace its own DOM/vision reading of the live page."""
    return _positive_int_env("PI_CONTEXT_BUDGET_TOKENS", _DEFAULT_CONTEXT_BUDGET_TOKENS)


def context_build_timeout_s() -> int:
    """PI_CONTEXT_BUILD_TIMEOUT_S — soft wall-clock budget for
    pi_context.build_project_brief(): between each of its query steps it
    checks elapsed time and stops adding further (lower-priority) sections
    once this is exceeded, returning whatever was already assembled rather
    than continuing to query. This is a cooperative check between bounded,
    LIMIT-capped queries, not a hard preemptive timeout — see
    pi_context.py's module docstring for why a true preemptive kill of a
    live DB session read is not something this codebase fakes safely
    without introducing a second DB session and a thread boundary for a
    feature that is explicitly supposed to stay simple and fail open."""
    return _positive_int_env("PI_CONTEXT_BUILD_TIMEOUT_S", _DEFAULT_CONTEXT_BUILD_TIMEOUT_S)


def semantic_search_enabled() -> bool:
    """PI_SEMANTIC_SEARCH_ENABLED — the Phase 5 (Scale) kill switch. Master-
    switch convention (unset/anything-but-1/true/yes means OFF), default
    OFF, matching PI_CRAWL_ENABLED/PI_CONTEXT_ENABLED/PI_HEAL_LEDGER —
    per this project's own Phase 5 kickoff decision (asked and confirmed
    directly, not inferred): every phase's rollout stays opt-in, and
    semantic search gets its OWN switch rather than riding in on
    PI_ENABLED, so turning it on is always a deliberate, separate choice.

    Also gates on app.models.project_intelligence.PiEmbedding actually
    being defined — see that module's `_PGVECTOR_AVAILABLE` guard. Checked
    here rather than at every call site so `semantic_search_enabled()` is
    the single source of truth for "is this feature actually usable right
    now," combining both the operator's choice (env var) and the
    environment's actual capability (package installed)."""
    if not pi_enabled():
        return False
    if os.environ.get("PI_SEMANTIC_SEARCH_ENABLED", "").strip().lower() not in (
        "1", "true", "yes",
    ):
        return False
    try:
        from app.models.project_intelligence import PiEmbedding

        return PiEmbedding is not None
    except Exception:
        return False


def rename_similarity_threshold() -> float:
    """PI_RENAME_SIMILARITY_THRESHOLD — the text-similarity bar a vanished
    and a newly-seen tier-5 component's labels must clear to be proposed as
    a candidate_rename pair (spec §18.4). Mirrors
    skill_store.get_fuzzy_match_threshold()'s pattern and default (0.93) —
    "deliberately biased against false merges", because a wrong pairing
    here can eventually propagate into a ledger correction if a reviewer
    isn't careful, exactly the failure mode §18.4 calls out by name."""
    raw = os.environ.get("PI_RENAME_SIMILARITY_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_RENAME_SIMILARITY_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_RENAME_SIMILARITY_THRESHOLD
    return value if 0.0 < value <= 1.0 else _DEFAULT_RENAME_SIMILARITY_THRESHOLD


# ── Identity tiers (spec §18.2) ──────────────────────────────────────────
# Locator strategies are named exactly as rf_listener.py / pi_ingest's own
# callers report them. Unknown/unrecognised strategies fall to tier 5
# (text-only) — the safe, least-trusting default rather than guessing high.
_TIER_BY_STRATEGY = {
    "data-testid": 1,
    "id": 2,
    "name": 3,
    "aria-label": 4,
    "role-position": 4,
    # SeleniumLibrary-style strategies the RF listener's own heuristic
    # locator classifier (workers/tasks/rf_listener.py) can emit for a
    # locator it can't positively identify as one of the above. A CSS
    # selector is often but not always id-anchored, so it lands in the
    # partial-trust tier rather than being assumed stable.
    "css": 4,
    "xpath": 5,
    "text": 5,
    "xpath-text": 5,
}


def classify_identity_tier(locator_strategy: Optional[str]) -> int:
    if not locator_strategy:
        return 5
    return _TIER_BY_STRATEGY.get(locator_strategy.strip().lower(), 5)


def compute_component_key(route: str, component_type: str, anchor: str) -> str:
    """Deterministic identity for a control (spec §18.2).

    Stable across a label change as long as `anchor` (the data-testid / id
    / name / aria-label+position / normalized text, in that preference
    order — see classify_identity_tier) is stable. This is the ENTIRE
    mechanism that makes a rename detectable rather than read as a
    delete+add; it is intentionally simple (a hash, not a lookup table) so
    it behaves identically whether called from the RF listener's Celery
    task or from Vibe-run ingestion.
    """
    raw = f"{route}\x1f{component_type}\x1f{anchor}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _normalize_route(url: Optional[str]) -> Optional[str]:
    """Path + query only — pi_screens.route is compared across environments
    with different hosts, so the host itself is deliberately dropped here.
    Returns None for an unparseable or empty URL rather than storing junk."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    route = parts.path or "/"
    if parts.query:
        route = f"{route}?{parts.query}"
    return route[:500]


# ── Robot Framework normalisation ────────────────────────────────────────

def normalize_rf_events(raw_events: list[dict]) -> list[dict[str, Any]]:
    """Turn the RF listener's buffered keyword events into an ORDERED list of
    screen visits:

        [{"route": str, "title": None, "actions": [
            {"component_type": str, "label": str, "locator": str,
             "locator_strategy": str, "outcome_success": bool}
        ]}]

    IMPORTANT — this is visit order, not route-dedup order. Consecutive
    events on the same route are folded into the current visit (no
    same-route no-op "edge" to itself), but a route seen again LATER, after
    the run moved elsewhere, starts a NEW entry rather than being merged
    back into its first appearance. pi_extract.process_capture_event walks
    this list pairwise to build pi_navigation_edges rows, so collapsing
    every visit to a route into one entry (as an earlier version of this
    function did) would silently discard every real transition after the
    first and fabricate a spurious one out of mere "which route happened to
    be seen first" — cross-project screen/component dedup already happens
    in pi_extract against the DB, so this function's only job is preserving
    the true sequence, never deduplicating it.

    Deliberately tolerant of malformed entries — one bad event must not
    drop the rest of the suite's capture. Events with no resolvable page
    URL are skipped (a route is the join key everything else hangs off).
    """
    visits: list[dict[str, Any]] = []
    skipped = 0

    for raw in raw_events or []:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        route = _normalize_route(raw.get("page_url"))
        if not route:
            skipped += 1
            continue
        if not visits or visits[-1]["route"] != route:
            visits.append({"route": route, "title": None, "actions": []})

        locator = raw.get("locator")
        if locator:
            visits[-1]["actions"].append({
                "component_type": str(raw.get("component_type") or "other")[:30],
                "label": str(raw.get("keyword_name") or locator)[:500],
                "locator": str(locator)[:2000],
                "locator_strategy": raw.get("locator_strategy"),
                "outcome_success": raw.get("status") != "FAIL",
            })

    if skipped:
        logger.info(
            "pi_ingest: skipped %d of %d RF capture event(s) with no resolvable page URL",
            skipped, len(raw_events or []),
        )
    return visits


# ── Vibe (AI) run normalisation ──────────────────────────────────────────
# NOTE ON CONFIDENCE: browser-use's AgentHistoryList item shape is exercised
# elsewhere in AEP (app/services/ai_runner.py) only via
# `item.model_output.current_state.next_goal`. `item.state.url` is
# browser-use's documented field for the page URL at that step, but is not
# read anywhere else in this codebase, so it is accessed defensively here
# (getattr with a None default at every level) rather than assumed present.
# A browser-use version bump that renames/removes it degrades this to "no
# screens observed from this run" rather than raising — capture must never
# be able to fail a run, and Vibe ingestion runs long after the run itself
# has already completed and persisted.

def normalize_vibe_history(history_json: Optional[str]) -> list[dict[str, Any]]:
    """Same visit-order contract as normalize_rf_events (see its docstring)
    — a route revisited later in the run starts a new entry rather than
    merging into its first appearance, so pi_extract's pairwise edge
    walk sees the real sequence of screens the agent moved through."""
    if not history_json:
        return []
    try:
        from browser_use.agent.views import AgentHistoryList

        history = AgentHistoryList.model_validate_json(history_json)
    except Exception:
        logger.warning("pi_ingest: could not parse Vibe run history_json — skipping capture",
                        exc_info=True)
        return []

    visits: list[dict[str, Any]] = []

    for item in getattr(history, "history", None) or []:
        try:
            state = getattr(item, "state", None)
            url = getattr(state, "url", None)
            route = _normalize_route(url)
            if not route:
                continue
            if not visits or visits[-1]["route"] != route:
                visits.append({"route": route, "title": None, "actions": []})

            model_output = getattr(item, "model_output", None)
            current_state = getattr(model_output, "current_state", None)
            description = getattr(current_state, "next_goal", None)
            actions = getattr(model_output, "action", None) or []
            for action in actions:
                # browser-use action objects are a discriminated union
                # (click_element / input_text / etc.) — read defensively
                # and record only what is present, rather than assuming a
                # specific action's field names.
                label = description or "Vibe action"
                visits[-1]["actions"].append({
                    "component_type": "other",
                    "label": str(label)[:500],
                    "locator": None,
                    "locator_strategy": "text",
                    "outcome_success": True,
                })
        except Exception:
            # One malformed step must not drop the rest of the run's
            # observations.
            logger.debug("pi_ingest: skipped one unparseable Vibe history step", exc_info=True)
            continue

    return visits


# ── Capture event bookkeeping ────────────────────────────────────────────

def write_capture_event(
    db,
    *,
    project_id,
    source_type: str,
    source_run_id=None,
    payload_json: Optional[dict] = None,
    payload_ref: Optional[str] = None,
):
    """Insert a pi_capture_events row and return it (not yet committed —
    caller controls the transaction so this can be written atomically with
    the extraction it triggers). Never raises: a failure here is logged and
    None is returned, which callers must treat as "nothing to process"."""
    from app.models.project_intelligence import PiCaptureEvent

    try:
        event = PiCaptureEvent(
            project_id=project_id,
            source_type=source_type,
            source_run_id=source_run_id,
            payload_json=payload_json,
            payload_ref=payload_ref,
        )
        db.add(event)
        db.flush()
        return event
    except Exception:
        logger.exception(
            "pi_ingest: could not write capture event for project %s (source=%s)",
            project_id, source_type,
        )
        return None
