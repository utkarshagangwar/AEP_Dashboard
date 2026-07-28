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
    fabricated figure."""
    if not prompt_tokens and not completion_tokens:
        return None
    litellm_model = _litellm_model_id(provider, model)
    if not litellm_model:
        return None
    try:
        import litellm

        cost = litellm.completion_cost(
            model=litellm_model,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
        )
        return float(cost) if cost is not None else None
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
    """Start of the current quota period for a provider. Google resets
    daily at midnight Pacific time (per Google's rate-limits docs); every
    other provider's period is "all time" — a rolling metered budget
    (AXON) or plain pay-as-you-go (Anthropic/OpenAI/OpenRouter) has no
    daily reset to anchor on."""
    if provider in _DAILY_RESET_PROVIDERS:
        now_pt = datetime.now(timezone(timedelta(hours=_PACIFIC_UTC_OFFSET_HOURS)))
        midnight_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight_pt.astimezone(timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def compute_key_usage(db) -> list[dict[str, Any]]:
    """One row per currently-configured key: usage in its current quota
    period, the effective limit (manual override, else AXON's auto
    default, else none), remaining, and a quota_status derived from real
    call outcomes when no numeric limit is known."""
    from sqlalchemy import func

    from app.models.ai_usage import AIKeyLimit, AIUsageEvent

    overrides = {row.key_label: row for row in db.query(AIKeyLimit).all()}
    results: list[dict[str, Any]] = []

    for provider, raw_key in _configured_keys():
        label = mask_key_label(provider, raw_key)
        period_start = _period_start(provider)

        agg = (
            db.query(
                func.count(AIUsageEvent.id),
                func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
            )
            .filter(
                AIUsageEvent.key_label == label,
                AIUsageEvent.source != "quota_probe",
                AIUsageEvent.created_at >= period_start,
            )
            .one()
        )
        calls_period, tokens_period, cost_period = agg
        cost_period = float(cost_period or 0)

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
            remaining = limit_value - calls_period
            resets_at = "midnight PT"
        elif limit_type == "budget_usd":
            remaining = round(limit_value - cost_period, 4)
            resets_at = "does not reset (metered budget)"

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
