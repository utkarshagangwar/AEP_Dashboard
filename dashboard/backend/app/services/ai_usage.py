"""AI Usage tracking — best-effort logging of every LLM call the platform
makes, plus per-key remaining-quota calculation for the AI Usage admin page.

Design summary (see app/models/ai_usage.py for the full rationale):
  - log_usage_event() is called from the two places LLM calls actually
    happen: llm_router.complete() (Judge/Brain/SOW — litellm-based) and
    ai_runner.py's provider-client builders (Hands — LangChain-based,
    consumed inside browser_use.Agent's own call loop). It NEVER raises —
    a usage-logging failure must never break the LLM call it's describing.
  - Key identification: every call is tagged with a masked key_label
    (mask_key_label()) — the provider name plus the last 6 characters of
    the actual secret. Never the raw key. Stable regardless of key
    rotation order, so usage for "the same key" aggregates correctly even
    if its position in a GOOGLE_API_KEYS list changes.
  - Cost: for the litellm-routed path, litellm's own maintained pricing
    table (litellm.completion_cost()) gives an accurate figure. For the
    direct-LangChain Hands path there's no litellm response object, so
    estimate_cost_usd() calls litellm's cost calculator in its standalone,
    token-count form against the closest litellm-recognized model id.
    For AXON specifically this is a labeled APPROXIMATION: AXON is ATG's
    own metered gateway in front of Gemini Flash with its own (unknown to
    us) markup over Google's list price, so its estimated cost is
    Google's gemini-flash-latest list price, not AXON's actual metered
    rate. Never presented as exact — see AIUsageEvent.extra / the
    "approx" flag callers can set.
  - Quota "remaining": Google's actual free-tier requests-per-day is not a
    fixed number we can safely hardcode (Google's own rate-limits docs
    explicitly say it varies by usage tier and account status and is "not
    guaranteed" — no static table is published anymore). So there is no
    auto-detected numeric Google limit here; Google's status is instead
    derived from the real outcome of the most recent call/probe for that
    key (see quota_status_for_key below). AXON's $10 metered budget IS a
    number the user configured themselves (see the AXON_API_KEY comment
    in .env), so it's the one provider with a genuine auto-detected
    numeric default (AXON_BUDGET_USD, still overridable). Every other
    provider/key is manual-override-only via the ai_key_limits table.
"""
from __future__ import annotations

import contextvars
import functools
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Providers whose free-tier daily quota resets at a fixed clock time rather
# than being a rolling/metered budget. Only Google today (see docstring).
_DAILY_RESET_PROVIDERS = {"google"}
_PACIFIC_UTC_OFFSET_HOURS = -7  # PDT; close enough for a "resets around midnight PT" label — not DST-exact

_AXON_DEFAULT_BUDGET_USD = float(os.environ.get("AXON_BUDGET_USD", "10") or "10")

# Providers with no known fixed cap unless the admin sets a manual override.
_NO_AUTO_LIMIT_PROVIDERS = {"anthropic", "openai", "openrouter"}


# ── Task grouping (AI Usage page, "Usage events by task") ──────────────────
#
# Every AI call is made many layers below the one user-triggered job that
# caused it — e.g. ingest_sow_task -> _analyze_part -> design_ingest -> ->
# llm_router.complete(), or run_ai_test_task -> ai_runner.run_ai_test_sync ->
# (dozens of browser-use agent steps, each its own LLM call). Threading an
# explicit run_type/run_id parameter through every one of those intermediate
# functions (grep shows 20+ llm_router.complete() call sites alone, several
# behind their own multi-level helper chains) would touch a large fraction
# of the codebase for a change that is purely "how do we label this event
# afterwards" — no call-routing behavior should change.
#
# A contextvar sidesteps that: whichever top-level Celery task started the
# job sets it once (see tracked_task() below), and every log_usage_event()
# call anywhere in that job's synchronous call stack — regardless of how
# many modules it passed through — picks it up automatically via
# _resolve_task_context(). contextvars are correctly thread-isolated (two
# Celery worker threads/processes running different tasks concurrently never
# see each other's value) and are inherited into asyncio Tasks created from
# the same thread (ai_runner.py's browser-use agent loop runs on
# `asyncio.new_event_loop().run_until_complete(...)` in the SAME thread that
# set the context, not a spawned OS thread — see _run_with_chromium), so the
# many LLM calls browser-use makes across one run's agent loop all land
# under the same task with no changes needed there either.
#
# NOT propagated into a genuinely new OS thread started without copying the
# context (none of the LLM call paths do this today). A call made outside
# any tracked_task() falls through with run_type=run_id=None, same as every
# event logged before this feature existed — see the "legacy" bucket in
# app.api.v1.ai_usage.list_task_groups.
_current_task: "contextvars.ContextVar[Optional[tuple[str, str]]]" = contextvars.ContextVar(
    "ai_usage_current_task", default=None
)


def tracked_task(run_type: str, id_index: int = 1):
    """Decorator for a Celery task function: every AI usage event logged
    anywhere during one call to it is attributed to (run_type, the task-id
    argument at position `id_index`) — see the module-level comment above.

    id_index is the task-id argument's position as Celery actually calls the
    function, 0-indexed. Every task wrapped with this uses `bind=True`, so
    index 0 is always the bound Task instance (`self`) and index 1 is the
    first real argument — the default. A couple of SOW tasks take
    (document_id, version_id) and want the more specific version_id as the
    task key (each regeneration attempt is its own task even though the
    document is the same), so those pass id_index=2 explicitly.

    Purely additive: never raises, never changes what the wrapped function
    receives or returns, and a task with no recognizable id argument (should
    never happen given the callers below, but defensive regardless) just
    runs unwrapped rather than failing the job over a labeling concern."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            task_id = args[id_index] if len(args) > id_index else None
            token = _current_task.set((run_type, str(task_id))) if task_id else None
            try:
                return fn(*args, **kwargs)
            finally:
                if token is not None:
                    try:
                        _current_task.reset(token)
                    except Exception:  # noqa: BLE001 — cleanup must never mask the real result
                        pass

        return wrapper

    return decorator


def _resolve_task_context(
    run_type: Optional[str], run_id: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """An explicit run_type/run_id always wins; otherwise fall back to
    whatever tracked_task() set for the currently-executing job."""
    if run_type or run_id:
        return run_type, run_id
    current = _current_task.get()
    return current if current else (None, None)


def mask_key_label(provider: str, key: Optional[str]) -> Optional[str]:
    """Stable, secret-safe identifier for a key: "<provider>:...<last 6 chars>".
    Never store or log the raw key itself."""
    if not key:
        return None
    tail = key.strip()[-6:]
    return f"{provider}:...{tail}"


def _redact(text: Optional[str], secrets: list[Optional[str]]) -> Optional[str]:
    """Strip any raw secret that might have leaked into an error message
    before it's persisted — defense in depth on top of mask_key_label."""
    if not text:
        return text
    redacted = text[:2000]
    for secret in secrets:
        if secret and len(secret) >= 8:
            redacted = redacted.replace(secret, "***")
    return redacted


def _litellm_model_id(provider: str, model: str) -> Optional[str]:
    """Map a (provider, model) pair to the model id litellm's cost
    calculator expects. AXON is approximated as the Gemini model it fronts
    (gemini-flash-latest) — see module docstring."""
    if provider == "anthropic":
        return f"anthropic/{model}"
    if provider == "google":
        return f"gemini/{model}"
    if provider == "axon":
        return f"gemini/{model}"
    if provider == "openai":
        return model
    if provider == "openrouter":
        return f"openrouter/{model}"
    return None


def estimate_cost_usd(
    provider: str,
    model: str,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> Optional[float]:
    """Best-effort cost estimate via litellm's pricing table. Returns None
    (never a guessed number) if the model isn't in litellm's map, token
    counts are missing, or litellm isn't importable — "unknown" beats a
    fabricated figure.

    Uses litellm.cost_per_token(), NOT litellm.completion_cost(). The
    installed litellm (1.74.x) removed completion_cost()'s standalone
    prompt_tokens=/completion_tokens= keyword form — that call now raises
    TypeError unconditionally, which this function's broad except silently
    swallowed into "no cost available" for every single call on this path
    (every Hands-path event: Google's _log_google_result/_log_google_error
    and AXON's UsageLoggingCallback both call this directly, never
    litellm.completion_cost(completion_response=...) — that form only runs
    on the litellm_router.py/_log_usage() path, which is why Judge/Brain/SOW
    costs still showed up while every Hands/AXON call priced at $0.00).
    cost_per_token(model=, prompt_tokens=, completion_tokens=) is litellm's
    still-supported lower-level API for exactly this "I already have the
    token counts, not a response object" shape, and returns
    (prompt_cost, completion_cost) — summed here into one figure."""
    if not prompt_tokens and not completion_tokens:
        return None
    litellm_model = _litellm_model_id(provider, model)
    if not litellm_model:
        return None
    try:
        import litellm

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=litellm_model,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
        )
        return float(prompt_cost) + float(completion_cost)
    except Exception:
        return None


def log_usage_event(
    *,
    source: str,
    provider: str,
    model: str,
    key: Optional[str] = None,
    key_label: Optional[str] = None,
    status: str,
    http_status: Optional[int] = None,
    error_message: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    cost_is_approx: bool = False,
    duration_ms: Optional[int] = None,
    attempts: Optional[int] = None,
    run_type: Optional[str] = None,
    run_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> None:
    """Write one usage event. Best-effort: any failure here is logged and
    swallowed, never raised — this must never break the LLM call it's
    describing (same reliability rule as the rest of this codebase's
    fire-and-forget logging)."""
    try:
        from app.core.database import SessionLocal
        from app.models.ai_usage import AIUsageEvent

        label = key_label or mask_key_label(provider, key)
        # Explicit run_type/run_id (none of today's call sites pass these)
        # always wins; otherwise pick up whatever tracked_task() set for the
        # job currently executing — see the module-level comment above it.
        run_type, run_id = _resolve_task_context(run_type, run_id)
        extra = {"cost_is_approx": True} if cost_is_approx else None

        with SessionLocal() as db:
            db.add(
                AIUsageEvent(
                    source=source,
                    provider=provider,
                    model=model,
                    key_label=label,
                    status=status,
                    http_status=http_status,
                    error_message=_redact(error_message, [key]),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    attempts=attempts,
                    run_type=run_type,
                    run_id=run_id,
                    created_by=created_by,
                    extra=extra,
                )
            )
            db.commit()
    except Exception as exc:  # noqa: BLE001 — logging must never break the caller
        logger.warning("ai_usage: failed to log usage event (%s/%s): %s", provider, model, exc)


@dataclass
class _CallTiming:
    started_at: float = field(default_factory=time.monotonic)


_UsageCallbackImpl = None  # built once, on first call — see UsageLoggingCallback()


def _get_usage_callback_class():
    """Lazily build (once) and cache the concrete BaseCallbackHandler
    subclass. A plain module-level factory function rather than a class
    with a custom __new__: returning a differently-typed instance from
    __new__ only gets __init__ auto-called by Python if that instance is
    actually an instance of the class being constructed, which a
    dynamically-built unrelated subclass is not — that's a real, silent
    "callback fires but self.provider/self.model were never set" bug, so
    don't repeat that shape here."""
    global _UsageCallbackImpl
    if _UsageCallbackImpl is not None:
        return _UsageCallbackImpl

    from langchain_core.callbacks import BaseCallbackHandler

    class _UsageLoggingCallbackImpl(BaseCallbackHandler):
        """Logs one ai_usage_events row per completed/failed call. Used
        for the single-key provider clients built in ai_runner.py
        (Anthropic/OpenAI/AXON/single-key Google) — the multi-key Google
        rotation path logs directly from _RotatingGoogleChat instead,
        since it needs to know which pool index actually served the call,
        which a generic callback can't see."""

        def __init__(self, *, provider: str, model: str, key_label: Optional[str], source: str = "hands"):
            super().__init__()
            self.provider = provider
            self.model = model
            self.key_label = key_label
            self.source = source
            self._timing = _CallTiming()

        def on_chat_model_start(self, *a, **kw):
            self._timing = _CallTiming()

        def on_llm_start(self, *a, **kw):
            self._timing = _CallTiming()

        def on_llm_end(self, response, **kw):
            duration_ms = int((time.monotonic() - self._timing.started_at) * 1000)
            prompt_tokens = completion_tokens = total_tokens = None
            try:
                generation = response.generations[0][0]
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if usage:
                    prompt_tokens = usage.get("input_tokens")
                    completion_tokens = usage.get("output_tokens")
                    total_tokens = usage.get("total_tokens")
                elif response.llm_output and response.llm_output.get("token_usage"):
                    tu = response.llm_output["token_usage"]
                    prompt_tokens = tu.get("prompt_tokens")
                    completion_tokens = tu.get("completion_tokens")
                    total_tokens = tu.get("total_tokens")
            except Exception:
                pass

            cost = estimate_cost_usd(self.provider, self.model, prompt_tokens, completion_tokens)
            log_usage_event(
                source=self.source,
                provider=self.provider,
                model=self.model,
                key_label=self.key_label,
                status="ok",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                cost_is_approx=(self.provider == "axon"),
                duration_ms=duration_ms,
            )

        def on_llm_error(self, error, **kw):
            duration_ms = int((time.monotonic() - self._timing.started_at) * 1000)
            http_status = getattr(error, "status_code", None) or getattr(error, "code", None)
            log_usage_event(
                source=self.source,
                provider=self.provider,
                model=self.model,
                key_label=self.key_label,
                status="error",
                http_status=http_status if isinstance(http_status, int) else None,
                error_message=str(error),
                duration_ms=duration_ms,
            )

    _UsageCallbackImpl = _UsageLoggingCallbackImpl
    return _UsageCallbackImpl


def UsageLoggingCallback(*, provider: str, model: str, key_label: Optional[str], source: str = "hands"):
    """Factory — build one usage-logging callback handler for a LangChain
    client's callbacks=[...] kwarg. Callable the same way a class
    constructor would be (ai_usage.UsageLoggingCallback(provider=...,
    model=..., key_label=...)); see _get_usage_callback_class() for why
    this is a function and not a class."""
    impl_cls = _get_usage_callback_class()
    return impl_cls(provider=provider, model=model, key_label=key_label, source=source)


# ── Per-key usage + remaining-quota calculation (AI Usage page) ────────────

def _get_key_list(env_plural: str, env_singular: str) -> list[str]:
    """Same convention as ai_runner._get_key_list / model_pool._get_key_list
    — duplicated rather than imported to keep this module import-independent
    (see model_pool.py's identical precedent)."""
    raw = os.environ.get(env_plural, "")
    if raw.strip():
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get(env_singular, "").strip()
    return [single] if single else []


def _configured_keys() -> list[tuple[str, str]]:
    """(provider, raw_key) for every key currently set in the environment,
    across every provider this platform can call."""
    out: list[tuple[str, str]] = []
    for provider, plural, singular in (
        ("anthropic", "ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEYS", "OPENAI_API_KEY"),
        ("google", "GOOGLE_API_KEYS", "GOOGLE_API_KEY"),
    ):
        for k in _get_key_list(plural, singular):
            out.append((provider, k))
    axon_key = os.environ.get("AXON_API_KEY", "").strip()
    if axon_key:
        out.append(("axon", axon_key))
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        out.append(("openrouter", openrouter_key))
    return out


def _period_start(provider: str) -> datetime:
    """Start of the current QUOTA period for a provider — what "Remaining"/
    quota_status are computed against in compute_key_usage, always, no
    matter what the admin has the display period picker set to (a cap is a
    fact about right now, not about whatever historical window someone's
    browsing). Google resets daily at midnight Pacific time (per Google's
    rate-limits docs); every other provider's quota period is "all time" —
    a rolling metered budget (AXON) or plain pay-as-you-go (Anthropic/
    OpenAI/OpenRouter) has no daily reset to anchor on."""
    if provider in _DAILY_RESET_PROVIDERS:
        now_pt = datetime.now(timezone(timedelta(hours=_PACIFIC_UTC_OFFSET_HOURS)))
        midnight_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight_pt.astimezone(timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _current_month_start() -> datetime:
    """Start of the current calendar month, Pacific time (same anchor as
    _period_start's daily boundary, for internal consistency). This is the
    default DISPLAY period for compute_key_usage's calls_period/
    tokens_period/cost_period_usd — deliberately a different concept from
    _period_start's QUOTA period (see compute_key_usage's docstring)."""
    now_pt = datetime.now(timezone(timedelta(hours=_PACIFIC_UTC_OFFSET_HOURS)))
    month_start_pt = now_pt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start_pt.astimezone(timezone.utc)


def compute_key_usage(
    db,
    *,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """One row per currently-configured key.

    Two independent aggregates per key — this is the important thing to
    understand before touching this function:
      - QUOTA aggregate (calls_quota/cost_quota below, internal — never
        returned directly) — always each provider's own _period_start:
        daily-at-midnight-PT for Google, all-time for a metered/pay-as-you-
        go budget (AXON, Anthropic/OpenAI/OpenRouter). Drives "Remaining"
        and quota_status, unconditionally, regardless of the arguments
        below — AXON's Remaining must stay the full $10 lifetime budget
        even while the table is displaying only last month's activity.
      - DISPLAY aggregate (calls_period/tokens_period/cost_period_usd —
        what the Per-key usage table's columns and its "total cost for
        this period" summary actually show) — defaults to the CURRENT
        CALENDAR MONTH for every provider uniformly when period_start/
        period_end are both omitted, overridable by the caller (the AI
        Usage page's period picker) to show any other month or custom
        range. Completely independent of the quota aggregate above.

    period_start/period_end override the display aggregate only.
    period_end is exclusive. Both None — every caller before this
    parameter existed (set_key_limit, clear_key_limit, and a bare GET
    /keys) — reproduces "current calendar month" for the display numbers
    while the quota numbers are entirely unaffected either way."""
    from sqlalchemy import func

    from app.models.ai_usage import AIKeyLimit, AIUsageEvent

    overrides = {row.key_label: row for row in db.query(AIKeyLimit).all()}
    is_filtered = period_start is not None or period_end is not None
    display_start = period_start if period_start is not None else _current_month_start()
    results: list[dict[str, Any]] = []

    def _aggregate(label: str, start: datetime, end: Optional[datetime]):
        filters = [
            AIUsageEvent.key_label == label,
            AIUsageEvent.source != "quota_probe",
            AIUsageEvent.created_at >= start,
        ]
        if end is not None:
            filters.append(AIUsageEvent.created_at < end)
        return (
            db.query(
                func.count(AIUsageEvent.id),
                func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.completion_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
            )
            .filter(*filters)
            .one()
        )

    for provider, raw_key in _configured_keys():
        label = mask_key_label(provider, raw_key)

        (
            calls_period,
            tokens_period,
            prompt_tokens_period,
            completion_tokens_period,
            cost_period,
        ) = _aggregate(label, display_start, period_end)
        cost_period = float(cost_period or 0)

        # Quota aggregate — separate query, separate (unfiltered) window.
        # See the docstring above for why this must never follow the
        # display period.
        calls_quota, _tp_q, _pt_q, _ct_q, cost_quota = _aggregate(
            label, _period_start(provider), None
        )
        cost_quota = float(cost_quota or 0)

        last_event = (
            db.query(AIUsageEvent)
            .filter(AIUsageEvent.key_label == label)
            .order_by(AIUsageEvent.created_at.desc())
            .first()
        )

        override = overrides.get(label)
        limit_type: Optional[str] = None
        limit_value: Optional[float] = None
        limit_source: Optional[str] = None

        if override:
            limit_type = override.limit_type
            limit_value = float(override.limit_value)
            limit_source = "manual"
        elif provider == "axon":
            limit_type = "budget_usd"
            limit_value = _AXON_DEFAULT_BUDGET_USD
            limit_source = "auto"

        remaining: Optional[float] = None
        resets_at: Optional[str] = None
        if limit_type == "requests_per_day":
            remaining = limit_value - calls_quota
            # A "resets at midnight PT" label reads as live/ongoing — hide
            # it once the admin is looking at a specific historical/custom
            # window rather than the live default, so it can't be misread
            # as describing that window.
            resets_at = None if is_filtered else "midnight PT"
        elif limit_type == "budget_usd":
            remaining = round(limit_value - cost_quota, 4)
            resets_at = None if is_filtered else "does not reset (metered budget)"

        if limit_value is not None:
            quota_status = "exhausted" if (remaining is not None and remaining <= 0) else "ok"
        elif last_event is None:
            quota_status = "unknown"
        elif last_event.status == "ok":
            quota_status = "ok"
        elif last_event.http_status in (401, 403, 429):
            quota_status = "exhausted"
        else:
            quota_status = "unknown"

        results.append(
            {
                "key_label": label,
                "provider": provider,
                "calls_period": int(calls_period or 0),
                "tokens_period": int(tokens_period or 0),
                "prompt_tokens_period": int(prompt_tokens_period or 0),
                "completion_tokens_period": int(completion_tokens_period or 0),
                "cost_period_usd": cost_period,
                "limit_type": limit_type,
                "limit_value": limit_value,
                "limit_source": limit_source,
                "remaining": remaining,
                "quota_status": quota_status,
                "resets_at": resets_at,
                "last_used_at": last_event.created_at if last_event else None,
            }
        )

    return results


# ── Task grouping — read side (AI Usage page, "Usage events by task") ──────

_LEGACY_RUN_TYPE = "__legacy__"  # synthetic marker for the grouping query only, never written to the DB

_TASK_KIND_LABELS = {
    "ai_run": "AI Test Run",
    "visual_audit": "Visual Audit",
    "orchestrator_run": "Autonomous QA",
    "sow_import": "SOW Import",
    "video_import": "Video Import",
    "sow_ledger": "SOW Ledger Extraction",
    "sow_generation": "SOW Draft Generation",
    "sow_impact": "SOW Impact Analysis",
}


def list_task_groups(
    db,
    *,
    provider: Optional[str] = None,
    source: Optional[str] = None,
    status_filter: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Aggregate ai_usage_events into one row per user-triggered task
    (run_type+run_id — see tracked_task()), applying the same filters the
    flat event list (list_usage_events in app.api.v1.ai_usage) uses.

    Events with no run_id — everything logged before this feature existed,
    or a call made outside any tracked_task() — are grouped into one
    synthetic "legacy" bucket per source instead of being dropped, so no
    historical data disappears from this page.

    Returns (groups, total_group_count) for pagination. Each group carries
    aggregates only, not its individual calls — those are fetched
    separately (GET /api/ai-usage filtered by run_type+run_id, or
    no_task=true&source=... for a legacy bucket) when the row is expanded,
    so this stays cheap however many calls a task made.
    """
    from sqlalchemy import case, func

    from app.models.ai_usage import AIUsageEvent

    query = db.query(AIUsageEvent).filter(AIUsageEvent.source != "quota_probe")
    if provider:
        query = query.filter(AIUsageEvent.provider == provider)
    if source:
        query = query.filter(AIUsageEvent.source == source)
    if status_filter:
        query = query.filter(AIUsageEvent.status == status_filter)
    if from_date:
        query = query.filter(AIUsageEvent.created_at >= from_date)
    if to_date:
        query = query.filter(AIUsageEvent.created_at < to_date)

    # Real tasks group by (run_type, run_id). Every NULL-run_id event is
    # folded into one synthetic per-source group (keyed by the sentinel
    # run_type below) rather than fragmenting into one row per call.
    group_run_type = func.coalesce(AIUsageEvent.run_type, _LEGACY_RUN_TYPE)
    group_run_id = case(
        (AIUsageEvent.run_id.isnot(None), AIUsageEvent.run_id),
        else_=AIUsageEvent.source,
    )

    rows = (
        query.with_entities(
            group_run_type.label("run_type"),
            group_run_id.label("run_id"),
            func.count(AIUsageEvent.id).label("call_count"),
            func.coalesce(func.sum(AIUsageEvent.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AIUsageEvent.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(AIUsageEvent.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AIUsageEvent.cost_usd), 0).label("cost_usd"),
            func.sum(case((AIUsageEvent.status == "error", 1), else_=0)).label("error_count"),
            func.min(AIUsageEvent.created_at).label("first_seen"),
            func.max(AIUsageEvent.created_at).label("last_seen"),
            func.array_agg(func.distinct(AIUsageEvent.source)).label("sources"),
            func.array_agg(func.distinct(AIUsageEvent.provider)).label("providers"),
        )
        .group_by(group_run_type, group_run_id)
        .order_by(func.max(AIUsageEvent.created_at).desc())
        .all()
    )

    total = len(rows)
    page = rows[offset : offset + limit]

    groups: list[dict[str, Any]] = []
    for r in page:
        is_legacy = r.run_type == _LEGACY_RUN_TYPE
        error_count = int(r.error_count or 0)
        call_count = int(r.call_count or 0)
        groups.append(
            {
                "run_type": None if is_legacy else r.run_type,
                "run_id": None if is_legacy else r.run_id,
                "is_legacy": is_legacy,
                # For a legacy group, group_run_id above resolved to the
                # source (there's no run_id to key on) — that's the only
                # thing that actually identifies it for the calls-list filter.
                "legacy_source": r.run_id if is_legacy else None,
                "call_count": call_count,
                "prompt_tokens": int(r.prompt_tokens or 0),
                "completion_tokens": int(r.completion_tokens or 0),
                "total_tokens": int(r.total_tokens or 0),
                "cost_usd": float(r.cost_usd or 0),
                "status": (
                    "ok" if not error_count
                    else "error" if error_count == call_count
                    else "partial"
                ),
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
                "sources": sorted(s for s in (r.sources or []) if s),
                "providers": sorted(p for p in (r.providers or []) if p),
            }
        )

    _attach_labels(db, groups)
    return groups, total


def _generic_label(run_type: Optional[str], run_id: Optional[str]) -> str:
    kind = _TASK_KIND_LABELS.get(run_type, run_type or "task")
    return f"{kind} · {(run_id or '')[:8]}"


def _attach_labels(db, groups: list[dict[str, Any]]) -> None:
    """Best-effort human-readable label per task group, batched one query
    per run_type present on this page (never one query per group). A
    lookup failure — deleted row, unexpected schema, import error — must
    never break the events page, so every branch degrades to a generic
    "<kind> · <short id>" label instead of raising."""
    for g in groups:
        if g["is_legacy"]:
            g["label"] = f"{g['legacy_source'] or 'unknown'} — calls with no task id (legacy)"
            g["task_kind_label"] = "Legacy"
            continue
        g["label"] = _generic_label(g["run_type"], g["run_id"])
        g["task_kind_label"] = _TASK_KIND_LABELS.get(g["run_type"], g["run_type"])

    try:
        _resolve_labels_by_type(db, groups)
    except Exception:  # noqa: BLE001 — a labeling bug must never break the page
        logger.exception("ai_usage: task label resolution failed; falling back to generic labels")


def _resolve_labels_by_type(db, groups: list[dict[str, Any]]) -> None:
    """One batched query per run_type actually present on this page of
    groups (never one query per group). Each block is independent — a
    schema/import problem in one run_type's lookup must not blank out
    labels already resolved for the others, so each is its own try/except
    rather than one wrapping the whole function."""
    import uuid as _uuid

    by_type: dict[str, list[dict[str, Any]]] = {}
    for g in groups:
        if not g["is_legacy"] and g["run_type"]:
            by_type.setdefault(g["run_type"], []).append(g)

    def _valid_uuids(items: list[dict[str, Any]]) -> list[Any]:
        out = []
        for it in items:
            try:
                out.append(_uuid.UUID(str(it["run_id"])))
            except (ValueError, TypeError):
                pass
        return out

    if "ai_run" in by_type:
        try:
            from app.models.ai_runs import AITestRun

            items = by_type["ai_run"]
            rows = (
                db.query(AITestRun.id, AITestRun.goal, AITestRun.platform)
                .filter(AITestRun.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.goal:
                    prefix = "Android" if row.platform == "android" else "Vibe Test"
                    g["label"] = f"{prefix}: {row.goal.strip()[:90]}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=ai_run")

    if "visual_audit" in by_type:
        try:
            from app.models.visual_qa import VisualRun

            items = by_type["visual_audit"]
            rows = (
                db.query(VisualRun.id, VisualRun.target_url)
                .filter(VisualRun.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.target_url:
                    g["label"] = f"Visual Audit: {row.target_url.strip()[:90]}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=visual_audit")

    if "orchestrator_run" in by_type:
        try:
            from app.models.orchestrator import OrchestratorRun

            items = by_type["orchestrator_run"]
            rows = (
                db.query(OrchestratorRun.id, OrchestratorRun.goal, OrchestratorRun.target_url)
                .filter(OrchestratorRun.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and (row.goal or row.target_url):
                    detail = (row.goal or row.target_url or "").strip()[:90]
                    g["label"] = f"Autonomous QA: {detail}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=orchestrator_run")

    artifact_types = [t for t in ("sow_import", "video_import") if t in by_type]
    if artifact_types:
        try:
            from app.models.visual_qa import DesignArtifact

            items = [g for t in artifact_types for g in by_type[t]]
            rows = (
                db.query(DesignArtifact.id, DesignArtifact.file_name)
                .filter(DesignArtifact.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.file_name:
                    kind = "SOW Import" if g["run_type"] == "sow_import" else "Video Import"
                    g["label"] = f"{kind}: {row.file_name}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type in %s", artifact_types)

    if "sow_ledger" in by_type:
        try:
            from app.models.sow import SowDocument, SowDocumentSource

            items = by_type["sow_ledger"]
            rows = (
                db.query(SowDocumentSource.id, SowDocument.title)
                .join(SowDocument, SowDocument.id == SowDocumentSource.document_id)
                .filter(SowDocumentSource.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.title:
                    g["label"] = f"SOW Ledger: {row.title}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=sow_ledger")

    if "sow_impact" in by_type:
        try:
            from app.models.sow import SowDocument, SowDocumentSource

            items = by_type["sow_impact"]
            rows = (
                db.query(SowDocumentSource.id, SowDocument.title)
                .join(SowDocument, SowDocument.id == SowDocumentSource.document_id)
                .filter(SowDocumentSource.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.title:
                    g["label"] = f"SOW Impact Analysis: {row.title}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=sow_impact")

    if "sow_generation" in by_type:
        try:
            from app.models.sow import SowDocument, SowDocumentVersion

            items = by_type["sow_generation"]
            rows = (
                db.query(
                    SowDocumentVersion.id,
                    SowDocument.title,
                    SowDocumentVersion.version_number,
                )
                .join(SowDocument, SowDocument.id == SowDocumentVersion.document_id)
                .filter(SowDocumentVersion.id.in_(_valid_uuids(items)))
                .all()
            )
            by_id = {str(r.id): r for r in rows}
            for g in items:
                row = by_id.get(str(g["run_id"]))
                if row and row.title:
                    g["label"] = f"SOW Draft v{row.version_number}: {row.title}"
        except Exception:  # noqa: BLE001
            logger.exception("ai_usage: label lookup failed for run_type=sow_generation")
