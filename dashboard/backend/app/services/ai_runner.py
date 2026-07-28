"""AI test execution engine — hybrid Playwright + browser-use runner.

Architecture (Phase 2.1 — Shared CDP Session):
  1. Launch Chromium with --remote-debugging-port=9222
  2. Wait for CDP endpoint at http://localhost:9222/json/version
  3. Playwright connects via chromium.connect_over_cdp(cdp_url)
  4. browser-use BrowserSession(cdp_url=cdp_url) shares the same instance
  5. Teardown: close Playwright connection, then terminate Chromium process

Port 9222 is reserved for AI test runs. Confirmed no AEP backend service
uses this port (AEP services: 8000/FastAPI, 5432/PostgreSQL, 6379/Redis).
"""
import asyncio
import base64
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from app.core.logging import get_logger

logger = get_logger(__name__)

_CDP_PORT = 9222
_CDP_TIMEOUT_S = 15

# A Google free-tier quota failure is normally valid for hours, rather than a
# one-off request failure.  Remembering it prevents every subsequent agent
# step from first spending time (and an LLM retry) on the already-exhausted
# key.  This is deliberately process-local: keys never leave the worker and a
# restarted worker safely rechecks them.  It also means a newly rotated key in
# the environment is immediately usable after a worker restart.
_GOOGLE_KEY_COOLDOWN_S = 21_600
_google_unavailable_until: dict[str, float] = {}
_google_key_state_lock = threading.Lock()

# Desktop viewport for AI test runs. Without an explicit window size,
# headless Chromium falls back to a small default viewport, which makes
# target sites render their mobile/responsive layout (hamburger menus,
# collapsed nav, etc.) instead of the desktop UI testers expect to see.
_VIEWPORT = {"width": 1530, "height": 820}

# Per-run viewport presets (2026-07-28, New Vibe Test Phase 2 — B.8's
# viewport half; Firefox/WebKit itself was deliberately left out, see
# _find_chromium's docstring for why). "desktop" is _VIEWPORT unchanged, so
# every existing caller that never passes viewport= keeps today's exact
# behavior. mobile/tablet let a Functional Test deliberately exercise a
# responsive breakpoint the AI-scoped agent would otherwise never see —
# same rationale as the desktop-viewport comment above, just at a different
# size. Public (no leading underscore): app/workers/tasks/ai_execution.py
# looks a run's stored viewport_preset up here.
VIEWPORT_PRESETS: dict[str, dict] = {
    "desktop": _VIEWPORT,
    "tablet": {"width": 810, "height": 1080},
    "mobile": {"width": 390, "height": 844},
}

# Injected as browser-use's `message_context` — a HumanMessage placed right
# after the system prompt and before the task, so it carries near-system
# priority without us having to fork/override browser-use's own
# system_prompt.md (which also defines the required JSON response schema —
# safer to add to it than replace it). browser-use's default prompt already
# tells the model to "handle popups/cookies" and "wait if not loaded", but
# only as generic one-liners; this makes that behavior the agent's default
# posture rather than something it only remembers under pressure, since a
# rigid, literal reading of the goal is what makes real runs brittle:
# a goal written for the happy path breaks the moment the live site shows
# something the goal text never mentioned.
_CONTENT_AWARE_GUIDANCE = (
    # Leading ":\n\n" matters: browser-use's MessageManager concatenates this
    # directly onto the literal string "Context for the task" with no
    # separator of its own (message_manager/service.py: 'Context for the
    # task' + message_context) — without it, this would run on as one
    # unbroken word ("...the taskOperating rules...").
    ":\n\n"
    "Operating rules for this task, in addition to your normal behavior:\n"
    "1. Never act blindly. Before every click, type, or navigation, look at "
    "the actual current page content (and screenshot) and confirm the "
    "element you're about to use really is what the goal implies — do not "
    "assume a button/field/label exists or says what the goal text expects "
    "just because the goal mentions it.\n"
    "2. If the page shows anything the goal did not describe — a cookie/"
    "consent banner, a modal, an ad, an unexpected redirect, a validation "
    "error, a session/login prompt, an empty or error state — do not ignore "
    "it and do not fail immediately. Resolve it first (dismiss, close, log "
    "in again, go back, retry) using your best judgment, then resume "
    "working toward the original goal.\n"
    "3. Always give the page time to finish loading before deciding an "
    "element is missing or an action failed: if you see a spinner, skeleton "
    "placeholder, disabled button, blank container, or a URL/tab still "
    "loading, wait and re-check the page state rather than concluding "
    "failure or clicking through it.\n"
    "4. If the live site differs slightly from how the goal is worded (a "
    "relabeled button, an extra confirmation step, a moved field, a "
    "slightly different flow), use judgment to accomplish the underlying "
    "intent of the goal rather than failing on a literal mismatch.\n"
    "5. If an action produces an error message, unexpected result, or "
    "no visible change, treat that as a signal to diagnose from the current "
    "page content — read the error, check what actually changed — and "
    "attempt a reasonable recovery (retry, undo, alternate path) before "
    "giving up on the goal.\n"
    # Rules 6-7 pair with max_actions_per_step=1 (see _max_actions_per_step
    # below). The structural limit already guarantees one action per
    # observation; stating it here stops the model from *planning* a batch it
    # cannot execute, which is what produced multi-action "next_goal" text
    # like "fill title, select type, enter experience and click Generate"
    # while the browser had only performed the first of the four.
    "6. Emit exactly ONE action per step. Do not batch several interactions "
    "into a single response and do not assume an action you have not yet "
    "performed already succeeded — after each action you will be shown the "
    "resulting page state, and only then do you decide the next action.\n"
    "7. Before repeating an action you already performed, check the "
    "evaluation of your previous goal and the current page. If the same "
    "action has already been attempted and the page did not change as "
    "expected, do NOT click it again — read the page for a validation error, "
    "a blocking modal, a disabled/loading control or a missing required "
    "field, resolve that, and only then retry.\n"
    "Only report the task as failed/done=false after you've genuinely tried "
    "to adapt to what the page is actually showing, not just what the goal "
    "text assumed it would show. Equally, do not call done=true (or describe "
    "the task as 'almost complete') until you have actually observed the "
    "final expected result on the page — a success message, the created "
    "record in a list, the expected navigation. An unverified assumption is "
    "not completion."
)

# One action per LLM step (browser-use default is 10).
#
# BUG FIX (2026-07-25) — "steps run ahead of the browser":
# browser-use's Agent.step() calls register_new_step_callback as soon as the
# model has DECIDED its actions, then hands the whole batch to multi_act(),
# which executes them back-to-back against ONE cached selector_map snapshot
# taken before the first action. With the stock limit of 10, a single step
# could decide "type title -> pick employment type -> type experience ->
# click Generate JD" and fire them all without ever re-reading the DOM in
# between. Two consequences, both observed in real runs:
#   1. The UI step list raced ahead of the live browser — one emitted step
#      represented up to ten queued interactions.
#   2. The agent's mental model of the page went stale mid-batch, so it kept
#      re-clicking controls ("Confirm & Create") whose effect it had never
#      actually observed, burning LLM calls and ending on a premature
#      "almost complete".
# Forcing 1 restores a strict observe -> decide -> execute -> observe loop:
# every emitted step maps to exactly one real browser action, and every
# decision is made against the page as it actually is.
#
# Override with AI_MAX_ACTIONS_PER_STEP only if you deliberately want the
# old batching behavior back (e.g. to trade accuracy for fewer LLM calls on
# a long, purely-navigational goal).
_DEFAULT_MAX_ACTIONS_PER_STEP = 1


def _max_actions_per_step() -> int:
    """Resolve the per-step action limit from AI_MAX_ACTIONS_PER_STEP.

    Falls back to _DEFAULT_MAX_ACTIONS_PER_STEP on a missing, non-numeric or
    non-positive value — a bad env var must degrade to the safe default, not
    crash the run or silently disable the limit.
    """
    import os

    raw = os.environ.get("AI_MAX_ACTIONS_PER_STEP", "").strip()
    if not raw:
        return _DEFAULT_MAX_ACTIONS_PER_STEP
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "AI_MAX_ACTIONS_PER_STEP=%r is not an integer — using default %d.",
            raw,
            _DEFAULT_MAX_ACTIONS_PER_STEP,
        )
        return _DEFAULT_MAX_ACTIONS_PER_STEP
    if value < 1:
        logger.warning(
            "AI_MAX_ACTIONS_PER_STEP=%d is invalid (must be >= 1) — using "
            "default %d.",
            value,
            _DEFAULT_MAX_ACTIONS_PER_STEP,
        )
        return _DEFAULT_MAX_ACTIONS_PER_STEP
    return value


async def _end_live_capture(
    capture_session, on_video_ready: Optional[Callable[[str], None]]
) -> None:
    """Stop an in-progress live capture (if any) and hand the finished
    video path to on_video_ready. Called right before every browser.close()
    in _execute_steps/_execute_replay_steps so cleanup runs on every exit
    path -- success, each early failure, and the final return -- without
    restructuring the existing per-step control flow. No-op (and never
    raises) when capture_session is None, i.e. every caller that didn't
    opt into enable_live_capture."""
    if capture_session is None:
        return
    try:
        from app.services.ai_run_capture import stop_capture

        video_path = await stop_capture(capture_session)
        if video_path and on_video_ready is not None:
            try:
                on_video_ready(video_path)
            except Exception:
                logger.exception("on_video_ready callback failed")
    except Exception:
        logger.exception("Failed to stop live capture cleanly")


# ── CDP helpers ──────────────────────────────────────────────────────────────
#
# Chromium-only, deliberately (New Vibe Test Phase 2 checklist item B.8,
# reviewed 2026-07-28): this whole module's architecture is built on
# Playwright's chromium.connect_over_cdp() sharing one CDP session with
# browser-use (see the module docstring at the top of this file). Playwright
# only exposes a CDP-based connect for Chromium — Firefox and WebKit use a
# different, non-CDP remote protocol in Playwright, so "add Firefox/WebKit"
# isn't a config flag here, it would need a second, differently-architected
# execution path (and likely a different mechanism for the live browser
# view, which is also built on this same CDP session). Deliberately not
# attempted in this pass — revisit only if cross-browser bugs actually start
# showing up in practice, per explicit product decision.

def _find_chromium() -> str | None:
    # 1. System PATH
    for name in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
        import os
        if os.path.exists(candidate):
            return candidate
    # 2. Playwright-managed Chromium binary (playwright install chromium puts it here)
    import glob as _glob
    for pattern in (
        "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        "/home/*/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    ):
        matches = _glob.glob(pattern)
        if matches:
            return sorted(matches)[-1]
    return None


def _wait_for_cdp_sync(port: int, timeout_s: int = _CDP_TIMEOUT_S) -> bool:
    """Synchronous poll for CDP readiness. Called before entering async context."""
    url = f"http://localhost:{port}/json/version"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


# ── Scoped agent helper (Phase 2.3) ─────────────────────────────────────────

def _get_key_list(env_name_plural: str, env_name_singular: str) -> list[str]:
    """Read a comma-separated list of API keys from env_name_plural (e.g.
    ANTHROPIC_API_KEYS), falling back to the older single-key env var
    (env_name_singular, e.g. ANTHROPIC_API_KEY) for backward compatibility
    with existing deployments that only have one key. Blank/whitespace-only
    entries are dropped."""
    import os

    raw = os.environ.get(env_name_plural, "")
    if raw.strip():
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get(env_name_singular, "").strip()
    return [single] if single else []


def _anthropic_client(model: str, keys: list[str]):
    """Build a plain (non-rotating) ChatAnthropic client from the first key.

    Reverted (2026-07-28) from a real key-rotating implementation
    (_build_rotating_chat_client, briefly present) back to this simple form
    at explicit request — this deployment has no Anthropic API key at all,
    so Anthropic is no longer in _build_llm()'s precedence chain below
    (AXON -> Google -> OpenRouter now); this function only remains because
    model_pool.py (the separate Autonomous QA orchestrator) still offers
    "anthropic" as one of its selectable providers and calls this directly
    with an explicit choice, unrelated to New Vibe Test runs.
    """
    from langchain_anthropic import ChatAnthropic

    from app.services import ai_usage

    logger.info(
        "AI runner: using Anthropic model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )
    return ChatAnthropic(
        model=model,
        api_key=keys[0],
        callbacks=[
            ai_usage.UsageLoggingCallback(
                provider="anthropic", model=model,
                key_label=ai_usage.mask_key_label("anthropic", keys[0]),
            )
        ],
    )


def _openai_client(model: str, keys: list[str]):
    """Build a plain (non-rotating) ChatOpenAI client from the first key.
    See _anthropic_client — same 2026-07-28 revert, same reason."""
    from langchain_openai import ChatOpenAI

    from app.services import ai_usage

    logger.info(
        "AI runner: using OpenAI model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )
    return ChatOpenAI(
        model=model,
        api_key=keys[0],
        callbacks=[
            ai_usage.UsageLoggingCallback(
                provider="openai", model=model,
                key_label=ai_usage.mask_key_label("openai", keys[0]),
            )
        ],
    )


def _openrouter_client(model: str, key: str):
    """Build a ChatOpenAI client pointed at OpenRouter's OpenAI-compatible
    endpoint (2026-07-28) — added to _build_llm()'s own precedence chain so
    a plain New Vibe Test run can reach OpenRouter too, not just the
    Autonomous QA orchestrator's model_pool.py (which already used this
    exact mechanism, verified working against google/gemma-4-26b-a4b-it:free
    — see model_pool.py's own comment). No per-key rotation: OpenRouter is
    configured with a single OPENROUTER_API_KEY, same convention as AXON.
    """
    from langchain_openai import ChatOpenAI

    from app.services import ai_usage

    logger.info("AI runner: using OpenRouter model %s", model)
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
        model=model,
        callbacks=[
            ai_usage.UsageLoggingCallback(
                provider="openrouter", model=model,
                key_label=ai_usage.mask_key_label("openrouter", key),
            )
        ],
    )


def _google_error_status(exc: BaseException) -> int | None:
    """Extract an HTTP status from the several exception wrappers used by
    google-api-core, LangChain and browser-use."""
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, int):
            return value
    text = str(exc).lower()
    for status in (401, 403, 429):
        if f" {status}" in text or f"({status}" in text or f"status={status}" in text:
            return status
    return None


def _is_google_key_failure(exc: BaseException) -> bool:
    """Whether an exception means this Google key cannot serve this call.

    The installed SDK normally raises google.api_core exceptions, but it can
    wrap them in a generic LangChain/browser-use error (the UI then only sees
    ``LLM API call failed``).  Classifying both forms is what makes rotation
    reliable for the real agent path.
    """
    return _google_error_status(exc) in (401, 403, 429)


def _mark_google_key_unavailable(key: str) -> None:
    import os

    try:
        cooldown_s = int(os.environ.get("GOOGLE_KEY_COOLDOWN_S", _GOOGLE_KEY_COOLDOWN_S))
    except ValueError:
        cooldown_s = _GOOGLE_KEY_COOLDOWN_S
    with _google_key_state_lock:
        _google_unavailable_until[key] = time.monotonic() + max(1, cooldown_s)


def _google_key_is_available(key: str) -> bool:
    with _google_key_state_lock:
        until = _google_unavailable_until.get(key, 0)
        if until <= time.monotonic():
            _google_unavailable_until.pop(key, None)
            return True
        return False


def _google_client(model: str, keys: list[str]):
    """Build a per-request key-rotating ChatGoogleGenerativeAI client.

    Pinned to the langchain-google-genai 2.x line (not 4.x) because
    browser-use==0.1.40 hard-pins langchain-anthropic==0.3.3, which
    requires langchain-core<0.4.0 — incompatible with 4.x's
    langchain-core>=1.0.0 requirement. 2.x still uses google_api_key=
    and talks to the classic generateContent REST endpoint, which
    accepts new model IDs without an SDK bump.

    Rotation here is done by SUBCLASSING ChatGoogleGenerativeAI, so the
    returned object still IS a BaseChatModel and passes browser-use's
    strict pydantic validation — unlike LangChain's native
    .with_fallbacks(), which crashes Agent construction (a
    RunnableWithFallbacks isn't a BaseChatModel, and
    browser_use.Agent.__init__ validates its llm kwarg strictly as
    Optional[BaseChatModel]). Anthropic/OpenAI briefly had the same
    subclassing treatment (_build_rotating_chat_client) but it was removed
    2026-07-28 — this deployment has no key for either provider.
    On a rate-limit/auth error (429 ResourceExhausted, 403 PermissionDenied,
    401 Unauthenticated) mid-run, the call is retried on each remaining key
    before failing — free-tier Gemini quotas are per-key per-project, so a
    multi-key setup keeps a long agent run alive when one key runs dry
    partway through.

    max_retries=2 (default 6) so a throttled key fails over to the next
    key in seconds instead of stalling the run in exponential backoff.

    AXON escalation (2026-07-24): when EVERY configured Google key is
    exhausted/rejected — not just one — the call now falls back to the
    AXON metering gateway (if AXON_API_KEY is set) instead of raising.
    Real production data (ai_usage_events, source="hands") showed exactly
    this happening: with 3 Google keys configured, individual 429s were
    already being rotated past correctly, but once all 3 keys were
    simultaneously exhausted partway through a long run, the exception
    propagated straight out of browser-use's Agent and failed the whole
    run — "Agent failed: Error ...: LLM API call failed" — even though
    AXON_API_KEY was configured and available the entire time.
    _build_llm()'s own Google->AXON fallback (see its docstring) only
    runs once, before the Agent is constructed, so it could never catch
    an exhaustion that happens mid-run after Google was initially picked.
    This is the mid-run equivalent of that same fallback. Previously this
    branch only wrapped multi-key setups in _RotatingGoogleChat; a single
    configured Google key returned a bare, non-rotating client with no
    escalation path at all, so it's folded in here too (a pool of one key
    behaves identically for the non-exhausted case — same calls, same
    result — the only change is what happens once that one key is
    exhausted).
    """
    from google.api_core.exceptions import (
        PermissionDenied,
        ResourceExhausted,
        Unauthenticated,
    )
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    from app.services import ai_usage

    # Same trigger set _google_pick_working_keys()/_key_probe() already
    # treat as "this key can't serve this request right now" (429/401/403).
    # _is_google_key_failure additionally handles the generic wrappers
    # emitted by LangChain/browser-use around those HTTP responses.
    _google_retry_exceptions = (ResourceExhausted, PermissionDenied, Unauthenticated)

    logger.info(
        "AI runner: using Google Gemini model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )

    def _log_google_result(key: str, result) -> None:
        prompt_tokens = completion_tokens = total_tokens = None
        try:
            message = result.generations[0].message
            usage = getattr(message, "usage_metadata", None)
            if usage:
                prompt_tokens = usage.get("input_tokens")
                completion_tokens = usage.get("output_tokens")
                total_tokens = usage.get("total_tokens")
        except Exception:
            pass
        cost = ai_usage.estimate_cost_usd("google", model, prompt_tokens, completion_tokens)
        ai_usage.log_usage_event(
            source="hands", provider="google", model=model,
            key_label=ai_usage.mask_key_label("google", key), status="ok",
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total_tokens, cost_usd=cost,
        )

    def _log_google_error(key: str, exc: BaseException) -> None:
        ai_usage.log_usage_event(
            source="hands", provider="google", model=model,
            key_label=ai_usage.mask_key_label("google", key), status="error",
            http_status=_google_error_status(exc) or 429, error_message=str(exc),
        )

    def _axon_fallback_client():
        """None when AXON_API_KEY isn't configured — the caller must treat
        that as "no escalation available" and re-raise the Google error."""
        import os

        if not os.environ.get("AXON_API_KEY", "").strip():
            return None
        return _axon_client()

    class _RotatingGoogleChat(ChatGoogleGenerativeAI):
        """ChatGoogleGenerativeAI that retries rate-limited calls on
        sibling clients (other API keys) before giving up.

        Usage is logged directly here (per pool index), not via a
        LangChain callback — a callback attached to the top-level model
        has no visibility into which sibling key actually served (or
        rejected) a given attempt, which is exactly the thing this class
        exists to hide from browser-use. See ai_usage.py's
        UsageLoggingCallback docstring for the single-key providers that
        don't have this problem.
        """

        def _set_pool(self, pool: list) -> None:
            # Bypass pydantic field validation for the private pool ref.
            object.__setattr__(self, "_pool", pool)

        def _set_keys(self, keys: list[str]) -> None:
            object.__setattr__(self, "_keys", keys)

        def _generate(self, *args, **kwargs):
            last_exc = None
            pool = getattr(self, "_pool", [self])
            keys = getattr(self, "_keys", [None] * len(pool))
            candidates = [
                (i, client) for i, client in enumerate(pool)
                if _google_key_is_available(keys[i])
            ]
            for i, client in candidates:
                key = keys[i]
                try:
                    result = ChatGoogleGenerativeAI._generate(client, *args, **kwargs)
                    _log_google_result(key, result)
                    return result
                except Exception as exc:
                    if not isinstance(exc, _google_retry_exceptions) and not _is_google_key_failure(exc):
                        raise
                    last_exc = exc
                    _mark_google_key_unavailable(key)
                    _log_google_error(key, exc)
                    logger.warning(
                        "Google key %d rate-limited mid-run — rotating to next key.",
                        i + 1,
                    )
            # Every configured Google key rejected this call — try AXON
            # before failing the whole agent run. See the class-level
            # "AXON escalation" docstring note above for why this exists.
            axon = _axon_fallback_client()
            if axon is not None:
                logger.warning(
                    "All %d Google key(s) exhausted mid-run (%s) — falling "
                    "back to AXON gateway.",
                    len(pool),
                    type(last_exc).__name__,
                )
                try:
                    return ChatOpenAI._generate(axon, *args, **kwargs)
                except Exception:
                    logger.exception(
                        "AXON fallback also failed after Google exhaustion — "
                        "surfacing the original Google error."
                    )
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("All configured Google API keys are in cooldown and AXON fallback is unavailable.")

        async def _agenerate(self, *args, **kwargs):
            last_exc = None
            pool = getattr(self, "_pool", [self])
            keys = getattr(self, "_keys", [None] * len(pool))
            candidates = [
                (i, client) for i, client in enumerate(pool)
                if _google_key_is_available(keys[i])
            ]
            for i, client in candidates:
                key = keys[i]
                try:
                    result = await ChatGoogleGenerativeAI._agenerate(
                        client, *args, **kwargs
                    )
                    _log_google_result(key, result)
                    return result
                except Exception as exc:
                    if not isinstance(exc, _google_retry_exceptions) and not _is_google_key_failure(exc):
                        raise
                    last_exc = exc
                    _mark_google_key_unavailable(key)
                    _log_google_error(key, exc)
                    logger.warning(
                        "Google key %d rate-limited mid-run — rotating to next key.",
                        i + 1,
                    )
            axon = _axon_fallback_client()
            if axon is not None:
                logger.warning(
                    "All %d Google key(s) exhausted mid-run (%s) — falling "
                    "back to AXON gateway.",
                    len(pool),
                    type(last_exc).__name__,
                )
                try:
                    return await ChatOpenAI._agenerate(axon, *args, **kwargs)
                except Exception:
                    logger.exception(
                        "AXON fallback also failed after Google exhaustion — "
                        "surfacing the original Google error."
                    )
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("All configured Google API keys are in cooldown and AXON fallback is unavailable.")

    clients = [
        _RotatingGoogleChat(model=model, google_api_key=k, max_retries=2)
        for k in keys
    ]
    for c in clients:
        c._set_pool(clients)
        c._set_keys(keys)
    return clients[0]


_AXON_DEFAULT_BASE_URL = "https://gw.atg.party/v1"
_AXON_DEFAULT_MODEL = "gemini-flash-latest"

# Same default as model_pool.py's _DEFAULT_OPENROUTER_MODEL (kept as a
# separate constant, not imported, so this module never has to import
# FROM model_pool.py — see model_pool.py's own docstring on that direction
# being one-way). Free tier, already verified working with a live call —
# see model_pool.py's to_langchain_client() OpenRouter branch comment.
_DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"


def _axon_client(model: str = _AXON_DEFAULT_MODEL):
    """Build the primary AXON client, with Gemini as its runtime fallback.

    AXON is an OpenAI-compatible proxy in front of Gemini Flash (see
    llm_router._resolve_call_target / design_ingest._complete_via_brain,
    which use the same gateway for other subsystems) — so it's wired in
    here via langchain_openai.ChatOpenAI with a custom base_url/api_key,
    the same mechanism model_pool.to_langchain_client() already uses for
    OpenRouter.

    AXON is the default Hands provider. If it rejects a request (including a
    depleted gateway balance), the same BaseChatModel-compatible wrapper
    switches that request to the resolved Gemini key pool. This protects a
    run already in progress without making browser-use consume an unsupported
    Runnable fallback wrapper.
    """
    import os

    from langchain_openai import ChatOpenAI

    from app.services import ai_usage

    base_url = os.environ.get("AXON_BASE_URL", "").strip() or _AXON_DEFAULT_BASE_URL
    api_key = os.environ.get("AXON_API_KEY", "").strip()
    logger.info("AI runner: using AXON gateway model %s (base_url=%s)", model, base_url)
    class _AxonPrimaryChat(ChatOpenAI):
        def _google_fallback(self):
            cached = getattr(self, "_google_fallback_client", None)
            if cached is not None:
                return cached
            resolved = resolve_google_provider()
            if not resolved:
                return None
            google_model, keys = resolved
            cached = _google_client(google_model, keys)
            object.__setattr__(self, "_google_fallback_client", cached)
            return cached

        @staticmethod
        def _should_fallback(exc: BaseException) -> bool:
            # AXON's exhausted-budget response is HTTP 402; 401/403/429 are
            # unusable-key/rate-limit equivalents. Do not mask programming,
            # validation or provider-contract errors with a second provider.
            return _google_error_status(exc) in (401, 402, 403, 429)

        def _generate(self, *args, **kwargs):
            try:
                return ChatOpenAI._generate(self, *args, **kwargs)
            except Exception as exc:
                if not self._should_fallback(exc):
                    raise
                google = self._google_fallback()
                if google is None:
                    raise
                logger.warning("AXON request failed (HTTP %s); using Gemini fallback.", _google_error_status(exc))
                return google._generate(*args, **kwargs)

        async def _agenerate(self, *args, **kwargs):
            try:
                return await ChatOpenAI._agenerate(self, *args, **kwargs)
            except Exception as exc:
                if not self._should_fallback(exc):
                    raise
                google = self._google_fallback()
                if google is None:
                    raise
                logger.warning("AXON request failed (HTTP %s); using Gemini fallback.", _google_error_status(exc))
                return await google._agenerate(*args, **kwargs)

    return _AxonPrimaryChat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        callbacks=[
            ai_usage.UsageLoggingCallback(
                provider="axon", model=model,
                key_label=ai_usage.mask_key_label("axon", api_key),
            )
        ],
    )


def _key_probe(url: str, headers: dict) -> bool:
    """Cheap auth check against a provider's list-models endpoint.

    Returns False only on an explicit 401/403 (invalid key). Any other
    outcome — success, rate limit, network error — returns True (fail-open)
    so a transient problem never disables a working provider."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code not in (401, 403)
    except Exception:
        return True


def _anthropic_key_valid(key: str) -> bool:
    return _key_probe(
        "https://api.anthropic.com/v1/models",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )


def _openai_key_valid(key: str) -> bool:
    return _key_probe(
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {key}"},
    )


def _axon_key_valid(key: str, base_url: str) -> bool:
    """Same pre-flight probe as the other three providers (see _key_probe).

    _build_llm() previously skipped this for AXON and returned _axon_client()
    unconditionally whenever AXON_API_KEY was non-empty, regardless of
    whether the gateway actually accepted it. An invalid/expired AXON key
    then only surfaced mid-run, as an opaque browser-use "Agent failed:
    Error 401: LLM API call failed" — the exact failure mode the other
    three providers' probes exist to prevent (see _build_llm() docstring).
    The per-call AXON->Google fallback in _axon_client()'s _AxonPrimaryChat
    softens this but doesn't fix it: it only fires if a Google fallback
    happens to be configured and healthy at that moment, so a bad AXON key
    fails the whole run rather than falling through to the next provider up
    front like Anthropic/OpenAI/Google already do.
    """
    return _key_probe(
        f"{base_url.rstrip('/')}/models",
        {"Authorization": f"Bearer {key}"},
    )


def _google_key_valid(key: str) -> bool:
    return _key_probe(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        {},
    )


def _google_pick_working_keys(model: str, keys: list[str]) -> list[str]:
    """Reorder Google keys so a key that can actually serve `model` right now
    comes first. Free-tier Gemini quotas are per-key per-model per-day, so
    the first configured key being exhausted (429) used to fail the whole
    run even when other keys had quota left before _RotatingGoogleChat
    existed to retry other configured keys mid-call.

    Each candidate is probed with a 1-token generateContent call. Keys that
    return 429/401/403 are moved to the back; any other outcome (success,
    network error) accepts the key (fail-open). Returns [] when every key
    is exhausted so the caller can try a different model instead."""
    import json as _json

    from app.services import ai_usage

    for i, key in enumerate(keys):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
        body = _json.dumps(
            {
                "contents": [{"parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 1},
            }
        ).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        # Logged as source="quota_probe" (excluded from the main usage
        # totals) purely so the AI Usage page can show an accurate
        # ok/exhausted status for each Google key without any *extra*
        # network calls — this probe already happens on every Hands run
        # regardless of whether usage tracking exists.
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            ai_usage.log_usage_event(
                source="quota_probe", provider="google", model=model,
                key_label=ai_usage.mask_key_label("google", key),
                status="error", http_status=exc.code,
            )
            if exc.code in (429, 401, 403):
                logger.warning(
                    "Google key %d/%d unusable for %s right now (HTTP %d) — "
                    "trying next key.",
                    i + 1,
                    len(keys),
                    model,
                    exc.code,
                )
                continue
        except Exception:
            pass
        else:
            ai_usage.log_usage_event(
                source="quota_probe", provider="google", model=model,
                key_label=ai_usage.mask_key_label("google", key), status="ok",
            )
        return keys[i:] + keys[:i]

    logger.warning(
        "All %d Google key(s) are exhausted or rejected for %s.", len(keys), model
    )
    return []


_GOOGLE_MODEL_PREFS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash"]


def resolve_google_provider(
    model_override: Optional[str] = None,
) -> Optional[tuple[str, list[str]]]:
    """Find a Google Gemini (model, keys) pair that can actually serve a
    request right now.

    Shared by _build_llm() below and model_pool.to_langchain_client() (the
    orchestrator's Google branch) so both paths get the same live-key-
    validation and per-model quota probing — before this was extracted,
    model_pool had its own naive path (no validation, stale default model),
    which would have silently reintroduced an already-fixed bug the moment
    the orchestrator was wired into the plain test-run flow.

    Returns None if no Google key is configured, the first key is rejected
    outright (401/403), or every candidate model's quota is exhausted for
    every key.
    """
    google_keys = _get_key_list("GOOGLE_API_KEYS", "GOOGLE_API_KEY")
    if not google_keys:
        return None
    valid_keys = [key for key in google_keys if _google_key_valid(key)]
    if not valid_keys:
        logger.warning(
            "GOOGLE_API_KEY(S) configured but every key was rejected by the API."
        )
        return None

    model_prefs = [model_override] if model_override else _GOOGLE_MODEL_PREFS
    for google_model in model_prefs:
        usable_keys = _google_pick_working_keys(google_model, valid_keys)
        if usable_keys:
            return google_model, usable_keys

    logger.warning(
        "GOOGLE_API_KEY(S) valid but daily quota is exhausted for all "
        "candidate models (%s).",
        ", ".join(model_prefs),
    )
    return None


def _build_llm():
    """
    Build a langchain LLM for the browser-use Agent.

    Provider precedence (2026-07-28 — Anthropic/OpenAI removed, this
    deployment has no key for either; OpenRouter added):
      1. AXON       → ChatOpenAI (AXON gateway, gemini-flash-latest) — primary
      2. Google     → ChatGoogleGenerativeAI, real per-key rotation
                       (_RotatingGoogleChat, see _google_client above)
      3. OpenRouter → ChatOpenAI (openrouter.ai, default
                       google/gemma-4-26b-a4b-it:free — already verified
                       working, see model_pool.py's matching comment)

    AXON is deliberately first for every browser-use agent. This avoids
    starting a long interactive run on Gemini's low free-tier quota and only
    discovering a quota/auth problem after it has already made progress.
    Google remains a fallback when AXON is unavailable or exhausted, and
    OpenRouter is the last resort before giving up.

    Google supports multiple keys as a comma-separated GOOGLE_API_KEYS list
    (falls back to single-key GOOGLE_API_KEY) to rotate across them when one
    hits a rate limit. AXON/OpenRouter are each configured with one key
    (AXON_API_KEY / OPENROUTER_API_KEY) — no rotation, same convention as
    before.

    Anthropic/ChatAnthropic and OpenAI/ChatOpenAI support still exists in
    _anthropic_client/_openai_client purely for model_pool.py (the separate
    Autonomous QA orchestrator, which still offers them as selectable
    providers) — this function no longer calls either, since New Vibe Test
    runs should never attempt a provider with no configured key.

    Override the model name with AI_LLM_MODEL env var.
    Raises RuntimeError if no key is found so the caller can surface a clear message.
    """
    import os
    model_override = os.environ.get("AI_LLM_MODEL", "")

    # Each provider's key is probed with a cheap list-models call before the
    # provider is chosen: an invalid key (401/403) used to surface only
    # mid-run as an opaque "Agent failed: Error code: 401 ... invalid
    # x-api-key" failure, even when a later provider in the chain had a
    # perfectly good key. Probes fail open on any non-auth error.
    any_configured = False

    axon_key = os.environ.get("AXON_API_KEY", "").strip()
    if axon_key:
        any_configured = True
        axon_base_url = os.environ.get("AXON_BASE_URL", "").strip() or _AXON_DEFAULT_BASE_URL
        if _axon_key_valid(axon_key, axon_base_url):
            return _axon_client()
        logger.warning(
            "AXON_API_KEY configured but rejected by the gateway (invalid "
            "key) — skipping AXON and trying the next provider."
        )

    google_keys = _get_key_list("GOOGLE_API_KEYS", "GOOGLE_API_KEY")
    if google_keys:
        any_configured = True
        # Free-tier Gemini quotas are per-model per-key per-day (only ~20
        # requests/day/model on this tier), so a fixed model choice dies
        # for the rest of the day once one long run exhausts it — override
        # with AI_LLM_MODEL if you have paid quota.
        resolved = resolve_google_provider(model_override or None)
        if resolved:
            google_model, usable_keys = resolved
            return _google_client(google_model, usable_keys)
        logger.warning(
            "GOOGLE_API_KEY(S) configured but unusable right now (invalid key "
            "or free-tier quota exhausted for every candidate model) — trying "
            "OpenRouter before giving up."
        )

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        any_configured = True
        if _key_probe(
            "https://openrouter.ai/api/v1/models",
            {"Authorization": f"Bearer {openrouter_key}"},
        ):
            return _openrouter_client(
                model_override or _DEFAULT_OPENROUTER_MODEL, openrouter_key
            )
        logger.warning(
            "OPENROUTER_API_KEY configured but rejected by the API (invalid "
            "key)."
        )

    if any_configured:
        raise RuntimeError(
            "LLM API key(s) are configured but all were rejected by their "
            "providers (invalid/expired keys), or Google's free-tier quota is "
            "exhausted. Configure AXON_API_KEY as the primary provider or update "
            "GOOGLE_API_KEY(S) or OPENROUTER_API_KEY in your .env file."
        )
    raise RuntimeError(
        "No LLM API key configured. Set one of AXON_API_KEY, "
        "GOOGLE_API_KEY(S), or OPENROUTER_API_KEY in your .env "
        "file to enable AI test execution."
    )


async def resolve_with_ai(
    cdp_url: str,
    task: str,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict] = None,
    max_steps: int = 100,
    max_duration_s: Optional[int] = 600,
    on_step: Optional[Callable[[str, Optional[str]], None]] = None,
    llm_override: Optional[object] = None,
    allow_unrestricted_domains: bool = False,
    on_step_complete: Optional[Callable[[bool, Optional[str]], None]] = None,
    viewport: Optional[dict] = None,
    post_action_check: Optional[Callable[[], "asyncio.Future"]] = None,
) -> dict:
    """
    Scoped browser-use agent (Phase 2.3) — browser_use 0.1.40 API.

    Safety invariants:
    - max_steps (default 100) is a ceiling against runaway/looping goals, not
      meant to be the primary limiter for legitimate multi-action tasks —
      raised from an earlier default of 5, then 30, both of which were
      cutting off real multi-step goals before they could finish.
    - max_duration_s (default 600 = 10 minutes) is the actual wall-clock
      safety backstop: time is a better proxy than step count for "this
      goal is taking too long / costing too much", so it's now tracked
      independently of max_steps rather than derived from it. Pass None to
      disable this backstop entirely and let the agent run until it finishes
      (asyncio.wait_for treats timeout=None as "wait forever") — used by New
      Vibe Test runs (app/workers/tasks/ai_execution.py), which can
      legitimately be long, multi-page workflows.
    - use_vision=False when sensitive_data present (prevents credential leakage to LLM)
    - allowed_domains required when sensitive_data present, UNLESS the caller
      explicitly passes allow_unrestricted_domains=True (see below).
    - message_context is always set to _CONTENT_AWARE_GUIDANCE (2026-07-25):
      instructs the agent to read the actual page state before acting instead
      of assuming the goal's literal wording still matches what's on screen,
      to resolve unexpected UI (popups, banners, errors, login prompts) on
      its own before continuing, and to wait for in-progress loading instead
      of treating a not-yet-rendered page as a failure. Applies to every
      caller — there is no opt-out, since this is meant to be the agent's
      default posture, not a per-run choice.

    on_step: optional callback invoked synchronously after each internal agent
    action is decided (before it executes), so callers can surface live,
    granular progress instead of only seeing the aggregate pass/fail result
    once the whole goal finishes. Receives (description, screenshot_b64).
    Exceptions raised by the callback are caught and logged — they must
    never abort the underlying browser automation.

    on_step_complete: optional companion to on_step, invoked once that same
    action has ACTUALLY been executed against the live browser. Receives
    (ok, error). Together the pair gives callers a real step lifecycle
    ("running" when decided -> "passed"/"failed" when executed) instead of
    the previous decide-time-only signal, which reported a step as finished
    while the browser had not performed it yet. Paired with
    max_actions_per_step=1 (see _max_actions_per_step) this is exactly one
    callback per real browser action. Same guarantee as on_step: exceptions
    are caught and logged, never propagated into the automation.

    llm_override: an already-built LangChain BaseChatModel (e.g. from
    app.services.model_pool.to_langchain_client()) to use instead of the
    default Anthropic->OpenAI->Google precedence in _build_llm(). Used by
    the orchestrator to steer which model drives this run; every existing
    caller omits this and gets identical behavior to before.

    allow_unrestricted_domains: explicit, per-call opt-out of the
    allowed_domains requirement above. Default False preserves the original
    behavior for every existing caller (saved Credential Profiles keep the
    mandatory domain scope). Only app/workers/tasks/ai_execution.py's
    ad-hoc "Website with login" path sets this True — a deliberate,
    user-requested trade-off (2026-07-21): those runs type real credentials
    but have no saved allowlist, and internal SSO/enterprise-auth redirects
    (a login button hopping to a different subdomain or an internal auth
    host entirely — e.g. app.company.com -> intranet.company.com/sso) kept
    getting blocked by the same guard that's supposed to stop credential
    leakage to genuinely out-of-scope domains. With this flag set, the
    agent can follow ANY redirect the target page sends it to, so a
    malicious or compromised page could in principle trick the agent into
    submitting the typed credentials somewhere else — there is no longer a
    domain guardrail for that run. Saved Credential Profiles are unaffected
    and still require allowed_domains.

    viewport: {"width": int, "height": int} override for this run's browser
    window/context size — see VIEWPORT_PRESETS above. None (every existing
    caller) preserves today's fixed desktop _VIEWPORT exactly. Only
    app/workers/tasks/ai_execution.py's Functional Test path sets this
    (from AITestRun.viewport_preset), letting a test deliberately exercise
    a mobile/tablet responsive breakpoint.

    post_action_check: optional async callable invoked AFTER each action has
    actually executed against the browser and reported no mechanical error
    of its own. It returns a short error string if the application under
    test rendered an error state as a result of that action, or None.

    BUG FIX (2026-07-28) — "agent passes a step the app actually failed":
    until this hook existed, a step's verdict came solely from
    ActionResult.error, which only ever reports MECHANICAL failures (element
    not found, click threw, navigation timed out). A click that landed
    perfectly and then made the app render "Error: Failed to start question
    generation" produced no ActionResult.error at all, so the step was
    recorded as passed and the error text never entered the step record, the
    post-run GEval judge's input, or the UI. This hook is the one place in
    the loop that can see the page as the application actually left it, so
    it is where an app-level error has to be caught.

    Contract: the hook is awaited inside the action wrapper, its exceptions
    are caught and logged, and it can never change control flow — a hook
    that fails behaves exactly like a hook that found nothing. Callers that
    omit it (every existing caller) keep today's behavior byte for byte.
    See app.services.error_detection.RunErrorWatcher for the implementation
    used by _execute_steps.

    Returns: {"success": bool, "action_summary": str, "duration_ms": int}
    """
    # Safety gate — bypassed only when the caller explicitly accepted the
    # credential-leak risk via allow_unrestricted_domains (see docstring).
    if sensitive_data and not allowed_domains and not allow_unrestricted_domains:
        raise ValueError(
            "allowed_domains must be provided when sensitive_data is set. "
            "Omitting it risks credential leakage to out-of-scope domains. "
            "Pass allow_unrestricted_domains=True to explicitly accept that "
            "risk instead."
        )

    # browser_use 0.1.40 API: Browser + BrowserConfig, no BrowserSession
    from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig  # lazy import

    if llm_override is not None:
        llm = llm_override
    else:
        try:
            llm = _build_llm()
        except RuntimeError as exc:
            return {"success": False, "action_summary": str(exc), "duration_ms": 0}

    start = time.monotonic()
    # Attach to the already-running Chromium via CDP. new_context_config only
    # applies if browser_use ends up creating a fresh context (normally it
    # reuses the context/page ai_runner.py already created and sized), but
    # setting it keeps the desktop viewport guaranteed either way.
    _active_viewport = viewport or _VIEWPORT
    browser = Browser(
        config=BrowserConfig(
            cdp_url=cdp_url,
            disable_security=True,
            new_context_config=BrowserContextConfig(
                browser_window_size=_active_viewport,
                no_viewport=False,
                # BUG FIX (2026-07-09): this was never set despite the
                # ValueError gate above claiming it was enforced -- the
                # allowed_domains param was validated then silently dropped,
                # so browser_use never actually restricted navigation.
                # This is the field browser_use itself checks (context.py:
                # BrowserContext._check_and_handle_navigation).
                allowed_domains=allowed_domains,
            ),
        )
    )

    agent_kwargs: dict = {
        "task": task,
        "llm": llm,
        "browser": browser,
        "message_context": _CONTENT_AWARE_GUIDANCE,
        # Strict observe -> decide -> execute -> observe loop. See
        # _max_actions_per_step's comment for the failure mode this fixes.
        "max_actions_per_step": _max_actions_per_step(),
    }
    if sensitive_data:
        agent_kwargs["sensitive_data"] = sensitive_data
        agent_kwargs["use_vision"] = False

    # Lightweight prompt-injection / scope-divergence guard (2026-07-28).
    # allowed_domains (when set) is already a hard technical control —
    # browser_use's own BrowserContextConfig.allowed_domains blocks
    # navigation outside it at the CDP level (see the 2026-07-09 fix above).
    # The gap this closes is specifically the allow_unrestricted_domains=True
    # path (ad-hoc "Website with login" runs — see this function's docstring):
    # there, sensitive_data is live-typed with NO domain allowlist at all, so
    # a compromised/malicious page could redirect the agent anywhere with no
    # technical control noticing. This can't safely auto-block (legitimate
    # SSO/enterprise-auth hops are exactly why allow_unrestricted_domains
    # exists), so it only flags: log a warning the moment the agent's own
    # next planned action targets a domain outside the ones already visited
    # in this run — a human reviewing the worker logs can catch a
    # credential-leak attempt this would otherwise never surface. (This
    # function has no run id in scope; the caller's own logging already
    # brackets these worker logs by run, e.g. ai_execution.py's task logs.)
    _seen_domains: set[str] = set()

    def _action_target_domains(model_output) -> list[str]:
        domains: list[str] = []
        for action in getattr(model_output, "action", None) or []:
            try:
                dumped = action.model_dump(exclude_unset=True)
            except Exception:
                continue
            for params in dumped.values():
                url = params.get("url") if isinstance(params, dict) else None
                if not url:
                    continue
                try:
                    host = urlsplit(url).hostname
                except Exception:
                    host = None
                if host:
                    domains.append(host.lower())
        return domains

    def _check_scope_divergence(model_output, n_steps: int) -> None:
        if not (sensitive_data and allow_unrestricted_domains):
            return
        try:
            for host in _action_target_domains(model_output):
                if _seen_domains and host not in _seen_domains:
                    logger.warning(
                        "AI run (unrestricted-domain, sensitive_data set): "
                        "step %d navigates to a new domain %r not seen "
                        "earlier in this run (seen so far: %s) — possible "
                        "off-script redirect; review this run's steps/video.",
                        n_steps, host, sorted(_seen_domains),
                    )
                _seen_domains.add(host)
        except Exception:
            logger.exception("Scope-divergence check failed at step %d", n_steps)

    if on_step is not None:

        async def _handle_new_step(state, model_output, n_steps) -> None:
            """browser-use calls this after each internal step is decided
            (before the action executes) — this is what makes step
            visibility genuinely live instead of only known at the end."""
            _check_scope_divergence(model_output, n_steps)
            try:
                try:
                    description = model_output.current_state.next_goal or f"Agent step {n_steps}"
                except Exception:
                    description = f"Agent step {n_steps}"
                screenshot_b64 = getattr(state, "screenshot", None)
                on_step(description, screenshot_b64)
            except Exception:
                logger.exception("AI step callback failed at step %d", n_steps)

        agent_kwargs["register_new_step_callback"] = _handle_new_step
    elif sensitive_data and allow_unrestricted_domains:
        # No on_step callback (e.g. Skill Replay's re-planned fallback path)
        # — still register the guard on its own so unrestricted-domain runs
        # get the warning even without live step streaming.
        async def _handle_new_step(state, model_output, n_steps) -> None:
            _check_scope_divergence(model_output, n_steps)

        agent_kwargs["register_new_step_callback"] = _handle_new_step

    agent = Agent(**agent_kwargs)

    # ── Post-execution hook ─────────────────────────────────────────────
    # browser-use 0.1.40 exposes no "action finished" callback — only
    # register_new_step_callback, which fires at DECISION time. Wrapping the
    # bound Agent.multi_act (a plain class, not a pydantic model, so instance
    # attribute assignment is safe) is the only place that reliably observes
    # "this step's action has now actually run against the browser". This is
    # what lets the caller keep its step list in lockstep with the live
    # browser instead of marking a step done the moment it was planned.
    # Installed when EITHER callback is wanted: post_action_check needs the
    # same "the action has now really run" moment that on_step_complete does,
    # and a caller may want error detection without live step streaming.
    if on_step_complete is not None or post_action_check is not None:
        _original_multi_act = agent.multi_act

        def _notify_complete(ok: bool, error: Optional[str]) -> None:
            if on_step_complete is None:
                return
            try:
                on_step_complete(ok, error)
            except Exception:
                logger.exception("AI step-completion callback failed")

        async def _run_post_action_check() -> Optional[str]:
            """Await the caller's app-error check. Swallows everything: this
            is an observability feature and must never abort a run."""
            if post_action_check is None:
                return None
            try:
                return await post_action_check()
            except Exception:
                logger.exception(
                    "Post-action application-error check failed; treating as "
                    "'no error found' so the run is unaffected"
                )
                return None

        async def _tracked_multi_act(actions, *args, **kwargs):
            try:
                results = await _original_multi_act(actions, *args, **kwargs)
            except BaseException as exc:
                # Includes InterruptedError (agent paused/stopped). Report the
                # step as not-completed and let the original exception through
                # untouched — this wrapper must never change control flow.
                _notify_complete(False, str(exc) or type(exc).__name__)
                raise
            error = next(
                (r.error for r in (results or []) if getattr(r, "error", None)), None
            )
            if error is None:
                # Only when the action itself was mechanically clean is it
                # worth asking "but did the APP break?" — if the action
                # already errored, that error is the more precise finding and
                # re-checking the page would just double-report it.
                error = await _run_post_action_check()
            _notify_complete(error is None, error)
            return results

        agent.multi_act = _tracked_multi_act

    timeout_s = max_duration_s
    try:
        # max_steps is a run()-level parameter in browser_use 0.1.40
        agent_run = agent.run(max_steps=max_steps)
        result = (
            await agent_run
            if timeout_s is None
            else await asyncio.wait_for(agent_run, timeout=float(timeout_s))
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        # AgentHistoryList is a plain pydantic model — it has no __bool__/__len__
        # override, so `bool(result)` is always True for any non-None return,
        # even when the agent never completed the goal or every step errored.
        # Use the library's own success signal instead.
        is_successful = result.is_successful() if result is not None else False
        success = bool(is_successful)

        # Serialize the agent history so a passed run can later be replayed
        # via Agent.rerun_history() without any LLM planning (skills).
        # Screenshots are stripped first — rerun doesn't need them and they
        # would bloat the stored JSON by megabytes.
        history_json = None
        if success and result is not None:
            try:
                for item in result.history:
                    state = getattr(item, "state", None)
                    if state is not None and getattr(state, "screenshot", None):
                        state.screenshot = None
                history_json = result.model_dump_json()
            except Exception:
                logger.exception("Failed to serialize agent history for skill capture")

        if success:
            summary = result.final_result() or "Agent completed the goal."
        elif result is not None and result.has_errors():
            errors = [e for e in result.errors() if e]
            summary = f"Agent failed: {errors[-1]}" if errors else "Agent encountered an error."
        elif is_successful is None:
            summary = "Agent did not finish the goal within max_steps."
        else:
            summary = result.final_result() or "Agent did not complete the goal."

        return {
            "success": success,
            "action_summary": summary,
            "duration_ms": duration_ms,
            "history_json": history_json,
        }
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "action_summary": (
                f"Agent timed out after {timeout_s}s "
                f"(max_duration_s={max_duration_s}, max_steps={max_steps})"
            ),
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"success": False, "action_summary": str(exc), "duration_ms": duration_ms}


# ── Main execution entry point ───────────────────────────────────────────────

async def _execute_steps(
    goal: str,
    environment_url: str,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict],
    max_steps: int,
    cdp_url: str,
    max_duration_s: Optional[int] = 600,
    on_event: Optional[Callable[[dict], None]] = None,
    llm_override: Optional[object] = None,
    cookies: Optional[list[dict]] = None,
    allow_unrestricted_domains: bool = False,
    run_id: Optional[str] = None,
    enable_live_capture: bool = False,
    on_video_ready: Optional[Callable[[str], None]] = None,
    viewport: Optional[dict] = None,
) -> dict:
    """Run the goal against the already-open CDP session.

    Steps are no longer a fixed 3-item list. There's always a deterministic
    nav step and a deterministic final-capture step, but the AI-scoped
    portion in between now emits one event per actual action the agent
    takes (via resolve_with_ai's on_step callback), so the step count
    reflects real work done instead of being capped at a single opaque step.

    on_event, if provided, is called synchronously with a copy of the event
    dict every time one is created or updated — this is what allows a caller
    (the Celery task) to persist events to the DB as they happen, so the SSE
    stream can surface them live instead of only after the whole run ends.
    Exceptions from on_event are caught and logged; they must never abort
    the underlying browser automation.

    cookies, if provided (from a kind="bypass" credential profile — see
    app/workers/tasks/ai_execution.py::_resolve_bypass_profile), is injected
    into the browser context as its own visible, failure-handled step, right
    after the context/page are established and before navigation — so the
    agent starts already authenticated and never sees the target app's login
    form (and whatever CAPTCHA may be guarding it) at all.

    enable_live_capture (default False, preserving today's behavior for
    every existing caller e.g. orchestrator.py's Hands step): when True,
    starts app.services.ai_run_capture's CDP-screencast live view + video
    recording on the page instead of taking per-step page.screenshot()s —
    see _end_live_capture and the screenshot_url=None branches below.
    run_id/on_video_ready are only meaningful when this is True.

    viewport: {"width": int, "height": int} override — see
    resolve_with_ai's docstring / VIEWPORT_PRESETS. None (every existing
    caller) preserves today's fixed desktop _VIEWPORT exactly.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "inconclusive",
            "summary": (
                "Execution engine unavailable: playwright library not installed. "
                "Add playwright==1.49.0 to requirements.txt and run "
                "'playwright install chromium' inside the backend container."
            ),
            "events": [],
            "failing_step": None,
        }

    browser_use_available = True
    try:
        import browser_use  # noqa: F401
    except ImportError:
        browser_use_available = False
        logger.warning("browser-use not installed — AI steps will use deterministic fallback")

    events: list[dict] = []
    start_ts = time.monotonic()
    seq_counter = {"n": 0}

    def elapsed_ms() -> int:
        return int((time.monotonic() - start_ts) * 1000)

    def _notify(ev: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(dict(ev))
        except Exception:
            logger.exception("on_event callback failed for step %s", ev.get("sequence"))

    def _emit(
        step_type: str,
        description: str,
        status: str = "running",
        screenshot_url: Optional[str] = None,
        is_failing: bool = False,
    ) -> dict:
        seq_counter["n"] += 1
        ev = {
            "sequence": seq_counter["n"],
            "status": status,
            "description": description,
            "step_type": step_type,
            "elapsed_ms": elapsed_ms(),
            "screenshot_url": screenshot_url,
            "highlighted_element": None,
            "is_failing_step": is_failing,
        }
        events.append(ev)
        _notify(ev)
        return ev

    def _update(ev: dict, **changes) -> None:
        ev.update(changes)
        _notify(ev)

    _active_viewport = viewport or _VIEWPORT

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        contexts = browser.contexts
        context = (
            contexts[0] if contexts else await browser.new_context(viewport=_active_viewport)
        )
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # Force the requested viewport (desktop by default) regardless of
        # how the context/page was created. CDP-attached Chromium can
        # already have a default context with no explicit viewport, which
        # renders sites in a small/responsive layout instead of the size
        # testers expect.
        try:
            await page.set_viewport_size(_active_viewport)
        except Exception:
            logger.warning("Failed to set viewport size to %s", _active_viewport, exc_info=True)

        # New Vibe Test / Skill Replay only (enable_live_capture=True) —
        # replaces the page.screenshot() calls below with a live CDP
        # screencast + recorded video. See _end_live_capture for the
        # matching stop, called right before every browser.close() in this
        # function so it runs on every exit path.
        capture_session = None
        if enable_live_capture and run_id:
            from app.services.ai_run_capture import start_capture

            capture_session = await start_capture(page, run_id)

        # ── In-run application-error detection (BUG FIX 2026-07-28) ──────
        # Watches the page for error states the APPLICATION UNDER TEST
        # renders — the failure mode ActionResult.error structurally cannot
        # see (see resolve_with_ai's post_action_check docstring and
        # app/services/error_detection.py for the full rationale).
        # Attached before any navigation so its console/network listeners
        # are live from the first request; baselined after navigation so
        # anything already on screen is never blamed on an agent action.
        # Best-effort: if construction fails, error_watcher stays None and
        # the run behaves exactly as it did before this feature existed.
        error_watcher = None
        try:
            from app.services.error_detection import RunErrorWatcher, detection_enabled

            if detection_enabled():
                error_watcher = RunErrorWatcher(context, page, run_id=run_id)
                await error_watcher.attach()
        except Exception:
            logger.warning(
                "Could not start in-run error detection (run_id=%s) — the run "
                "continues without it.",
                run_id, exc_info=True,
            )
            error_watcher = None

        def _app_errors() -> list[dict]:
            """Confirmed application errors seen so far. Included on EVERY
            return path (not just the success one) so a run that also failed
            mechanically still carries the app-error evidence downstream —
            app/workers/tasks/ai_execution.py's _persist_result reads this
            key to gate the run's final status."""
            return error_watcher.signals if error_watcher is not None else []

        # ── Step: inject bypass auth cookie (kind="bypass" profiles only) ──
        if cookies:
            # Land on the bare origin before injecting the cookie, matching
            # ig_automation's hopscotch_client.py sequence (open browser at
            # the app root -> inject cookie -> navigate to the real target
            # route). Injecting onto a still-blank page and then jumping
            # straight to the deep target route in one shot left the cookie
            # step reporting "passed" while the app still rendered its
            # login/email-entry screen -- reproduced 2026-07-24 against the
            # corrected GET /auth/admin-token contract and confirmed this
            # ordering was the remaining difference from the working RF run.
            if environment_url and environment_url != "about:blank":
                origin = urlunsplit(urlsplit(environment_url)[:2] + ("/", "", ""))
                try:
                    await page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    logger.warning(
                        "Pre-cookie origin navigation to %s failed; continuing to "
                        "cookie injection anyway",
                        origin,
                        exc_info=True,
                    )

            cookie_event = _emit("deterministic", "Inject authenticated session cookie")
            try:
                await context.add_cookies(cookies)
                _update(cookie_event, status="passed", elapsed_ms=elapsed_ms())
            except Exception as exc:
                logger.exception("Failed to inject bypass auth cookie(s): %s", exc)
                _update(
                    cookie_event, status="failed", elapsed_ms=elapsed_ms(), is_failing_step=True
                )
                if error_watcher is not None:
                    await error_watcher.detach()
                await _end_live_capture(capture_session, on_video_ready)
                await browser.close()
                return {
                    "status": "failed",
                    "summary": f"Step {cookie_event['sequence']} failed: {exc}",
                    "events": events,
                    "failing_step": cookie_event,
                    "app_errors": _app_errors(),
                }

        # ── Step: deterministic navigation ──────────────────────────────
        nav_event = _emit("deterministic", "Launch browser and navigate to application")
        try:
            if environment_url and environment_url != "about:blank":
                await page.goto(
                    environment_url, wait_until="domcontentloaded", timeout=30000
                )
            nav_shot_url = None
            if not enable_live_capture:
                shot = await page.screenshot()
                nav_shot_url = "data:image/png;base64," + base64.b64encode(shot).decode()
            _update(
                nav_event,
                status="passed",
                elapsed_ms=elapsed_ms(),
                screenshot_url=nav_shot_url,
            )
        except Exception as exc:
            logger.exception("Navigation step failed: %s", exc)
            b64 = None
            if not enable_live_capture:
                try:
                    shot = await page.screenshot()
                    b64 = "data:image/png;base64," + base64.b64encode(shot).decode()
                except Exception:
                    b64 = None
                    logger.warning(
                        "Failed to capture failure screenshot for navigation "
                        "step (run_id=%s) — failing step will have no visual "
                        "evidence.",
                        run_id, exc_info=True,
                    )
            _update(
                nav_event,
                status="failed",
                elapsed_ms=elapsed_ms(),
                screenshot_url=b64,
                is_failing_step=True,
            )
            if error_watcher is not None:
                await error_watcher.detach()
            await _end_live_capture(capture_session, on_video_ready)
            await browser.close()
            return {
                "status": "failed",
                "summary": f"Step {nav_event['sequence']} failed: {exc}",
                "events": events,
                "failing_step": nav_event,
                "app_errors": _app_errors(),
            }

        # Baseline AFTER navigation, BEFORE the agent acts: any error-looking
        # content already on the landing page (a stale-session warning, a
        # pre-broken widget) is recorded as pre-existing so it can never be
        # attributed to an agent action and turn every run on that page into
        # a false finding. Also clears the console/network buffers filled
        # during page load.
        if error_watcher is not None:
            await error_watcher.seed_baseline()

        # ── Step(s): AI-scoped goal — one live event per real agent action ──
        history_json: Optional[str] = None
        if browser_use_available:
            # Step lifecycle (2026-07-25). Previously each agent step was
            # emitted as status="passed" the instant the model decided it —
            # before the browser had performed anything — which is why the UI
            # step list visibly ran ahead of the live browser view. Now a step
            # is emitted as "running" at decision time and only resolved once
            # resolve_with_ai reports it actually executed. Event rows are
            # upserted by sequence (see ai_execution._upsert_ai_run_event), so
            # the second write updates the same row the SSE stream already
            # sent — no duplicate steps.
            pending_step: dict = {"event": None}
            # Holds the app-error signal produced by _post_action_check for
            # the step currently being resolved. Written by the hook (which
            # runs inside multi_act, immediately before the completion
            # callback) and consumed exactly once by _resolve_pending_step,
            # so a signal can never be attached to the wrong step.
            pending_app_error: dict = {"value": None}

            def _resolve_pending_step(ok: bool = True, error: Optional[str] = None) -> None:
                ev = pending_step["event"]
                signal = pending_app_error["value"]
                pending_app_error["value"] = None
                if ev is None:
                    return
                pending_step["event"] = None
                changes = {"status": "passed" if ok else "failed", "elapsed_ms": elapsed_ms()}
                if not ok and error:
                    # Keep the run-level failure verdict owned by
                    # agent_result below: an individual action can fail and
                    # be recovered from by the agent, so annotate the step
                    # without flagging it as THE failing step of the run.
                    #
                    # This appended text is also what makes the finding
                    # visible to the post-run GEval judge: ai_eval.evaluate_run
                    # builds its context from these descriptions, and
                    # step_sampling always keeps failed steps, so an app error
                    # recorded here always reaches the judge.
                    changes["description"] = f"{ev['description']} — {error}"
                if signal is not None:
                    # An application error IS the finding, not an incidental
                    # action hiccup — unlike a mechanical error above, flag
                    # it as a failing step so it survives step sampling, is
                    # highlighted in the UI, and can be picked up as the
                    # run's failing_step by _persist_result.
                    changes["is_failing_step"] = True
                    if signal.get("screenshot_url"):
                        # The only visual evidence available on live-capture
                        # runs, which deliberately skip per-step screenshots.
                        changes["screenshot_url"] = signal["screenshot_url"]
                    # Same dict object the watcher holds in .signals, so this
                    # back-fills the step number onto the record _app_errors()
                    # hands to _persist_result.
                    signal["sequence"] = ev["sequence"]
                _update(ev, **changes)

            def _on_agent_step(description: str, screenshot_b64: Optional[str]) -> None:
                # Safety net: a step that never reached execution (e.g. the
                # agent was paused between decision and multi_act) would
                # otherwise stay "running" forever in the UI.
                _resolve_pending_step(ok=True)
                shot_url = (
                    f"data:image/png;base64,{screenshot_b64}"
                    if screenshot_b64 and not enable_live_capture
                    else None
                )
                pending_step["event"] = _emit(
                    "ai_scoped", description, status="running", screenshot_url=shot_url
                )

            def _on_agent_step_complete(ok: bool, error: Optional[str]) -> None:
                _resolve_pending_step(ok=ok, error=error)

            async def _post_action_check() -> Optional[str]:
                """Ask the watcher whether the APP just errored. Returns a
                short message (which resolve_with_ai then treats exactly like
                an action error, failing the step) or None.

                Returning a message does NOT abort the agent: it keeps
                working the goal, so the rest of the test still runs and the
                report still covers every remaining step. The run-level
                consequence is applied once, after the fact, by
                _persist_result's app-error gate.
                """
                if error_watcher is None:
                    return None
                signal = await error_watcher.check()
                if not signal:
                    return None
                pending_app_error["value"] = signal
                return f"application error: {signal['message']}"

            try:
                agent_result = await resolve_with_ai(
                    cdp_url=cdp_url,
                    task=goal,
                    allowed_domains=allowed_domains,
                    sensitive_data=sensitive_data,
                    max_steps=max_steps,
                    max_duration_s=max_duration_s,
                    on_step=_on_agent_step,
                    llm_override=llm_override,
                    allow_unrestricted_domains=allow_unrestricted_domains,
                    on_step_complete=_on_agent_step_complete,
                    viewport=viewport,
                    post_action_check=_post_action_check,
                )
            except Exception as exc:
                logger.exception("AI-scoped execution raised unexpectedly: %s", exc)
                agent_result = {
                    "success": False,
                    "action_summary": str(exc),
                    "duration_ms": elapsed_ms(),
                }

            # Anything still open when the agent stopped (timeout, hard error,
            # max_steps) is resolved against the run's own verdict so no step
            # is left stuck as "running".
            _resolve_pending_step(ok=bool(agent_result.get("success")))

            if not agent_result["success"]:
                # ALWAYS capture this one frame, live capture or not (BUG FIX
                # 2026-07-28). It was previously skipped whenever a video was
                # being recorded, which meant a Functional Test — the only
                # flow that records video — produced NO still image anywhere
                # in the run. Two things depended on one and got nothing:
                # the UI's failing-step card (its image slot was always
                # empty) and, more importantly,
                # ai_eval.evaluate_expected_results, which needs a final
                # still to check the run's own Expected Results against and
                # therefore never ran at all. One frame per run is negligible
                # next to the video it sits beside.
                fail_shot_b64 = None
                fail_shot_url = None
                try:
                    shot = await page.screenshot()
                    fail_shot_b64 = base64.b64encode(shot).decode()
                    fail_shot_url = "data:image/png;base64," + fail_shot_b64
                except Exception:
                    logger.warning(
                        "Failed to capture failure screenshot for the "
                        "agent's failing step (run_id=%s) — "
                        "failing_step_screenshot_url will be empty in "
                        "the UI with no other trace of why.",
                        run_id, exc_info=True,
                    )
                fail_event = _emit(
                    "ai_scoped",
                    agent_result.get(
                        "action_summary", "AI agent could not complete the goal."
                    ),
                    status="failed",
                    screenshot_url=fail_shot_url,
                    is_failing=True,
                )
                if error_watcher is not None:
                    await error_watcher.detach()
                await _end_live_capture(capture_session, on_video_ready)
                await browser.close()
                return {
                    "status": "failed",
                    "summary": agent_result.get(
                        "action_summary", "AI agent could not complete the goal."
                    ),
                    "events": events,
                    "failing_step": fail_event,
                    "app_errors": _app_errors(),
                    "final_screenshot_b64": fail_shot_b64,
                }

            history_json = agent_result.get("history_json")
        else:
            # Deterministic fallback when browser-use is unavailable
            _emit(
                "ai_scoped",
                f"[browser-use not installed] {goal}",
                status="inconclusive",
            )
            if error_watcher is not None:
                await error_watcher.detach()
            await _end_live_capture(capture_session, on_video_ready)
            await browser.close()
            return {
                "status": "inconclusive",
                "summary": (
                    "browser-use library is not installed. "
                    "Add browser-use to requirements.txt and rebuild the container."
                ),
                "events": events,
                "failing_step": None,
                "app_errors": _app_errors(),
            }

        # ── Step: deterministic final capture ───────────────────────────
        verify_event = _emit("deterministic", "Capture final state and evaluate outcome")
        final_shot_b64 = None
        try:
            # ALWAYS capture the final frame, live capture or not (BUG FIX
            # 2026-07-28) — see the matching comment on the agent-failure
            # path above for why. This is the image
            # ai_eval.evaluate_expected_results grades the run's own
            # "Expected Results" against; without it that judge silently
            # never ran on a single Functional Test.
            #
            # It is still only attached to the step event when live capture
            # is OFF, so the step list keeps exactly today's appearance on
            # video-recorded runs (and one large base64 image per run is
            # kept out of the events table). The judge reads it from the
            # returned dict instead.
            shot = await page.screenshot()
            final_shot_b64 = base64.b64encode(shot).decode()
            verify_shot_url = (
                None if enable_live_capture else "data:image/png;base64," + final_shot_b64
            )
            _update(
                verify_event,
                status="passed",
                elapsed_ms=elapsed_ms(),
                screenshot_url=verify_shot_url,
            )
        except Exception as exc:
            logger.exception("Final capture step failed: %s", exc)
            _update(
                verify_event,
                status="failed",
                elapsed_ms=elapsed_ms(),
                is_failing_step=True,
            )
            if error_watcher is not None:
                await error_watcher.detach()
            await _end_live_capture(capture_session, on_video_ready)
            await browser.close()
            return {
                "status": "failed",
                "summary": f"Step {verify_event['sequence']} failed: {exc}",
                "events": events,
                "failing_step": verify_event,
                "app_errors": _app_errors(),
            }

        app_errors = _app_errors()
        if error_watcher is not None:
            await error_watcher.detach()
        await _end_live_capture(capture_session, on_video_ready)
        await browser.close()

    # status stays "passed" here even when app_errors is non-empty: the agent
    # did reach the end of its goal, and downgrading the verdict is a
    # persistence-layer decision (app/workers/tasks/ai_execution.py's
    # _persist_result), not this function's. Doing it here would also change
    # the contract for app/services/orchestrator.py, which calls
    # run_ai_test_sync and has its own separate result handling.
    return {
        "status": "passed",
        "summary": (
            f"All {len(events)} steps completed successfully."
            if not app_errors
            else (
                f"All {len(events)} steps ran, but the application showed "
                f"{len(app_errors)} error state(s) during the run — first: "
                f"{app_errors[0]['message']}"
            )
        ),
        "events": events,
        "failing_step": None,
        "history_json": history_json,
        "app_errors": app_errors,
        "final_screenshot_b64": final_shot_b64,
    }


def _run_with_chromium(
    runner: Callable[[str], "asyncio.Future"], viewport: Optional[dict] = None
) -> dict:
    """Launch Chromium with CDP, run an async runner(cdp_url) coroutine
    factory in a fresh event loop, tear the browser down. Shared by
    run_ai_test_sync and run_skill_replay_sync.

    viewport: {"width": int, "height": int} for the --window-size launch
    flag — see VIEWPORT_PRESETS. None (run_skill_replay_sync, and
    run_ai_test_sync's own default) preserves today's fixed desktop
    _VIEWPORT. Must match whatever viewport resolve_with_ai/_execute_steps
    set on the Playwright context for the same run, or the outer Chromium
    window and the inner page viewport would disagree.
    """
    chromium = _find_chromium()
    if not chromium:
        return {
            "status": "inconclusive",
            "summary": (
                "Chromium not found in PATH. Install chromium-browser in the "
                "backend container or run 'playwright install chromium'."
            ),
            "events": [],
            "failing_step": None,
        }

    cdp_url = f"http://localhost:{_CDP_PORT}"
    # No --headless: testing whether IG's app (or a bot-detection layer in
    # front of it) treats a headless/software-rendered session differently
    # from a headed one for the admin-bypass cookie flow -- the working
    # ig_automation RF run uses BROWSER=chrome (headed), while this was
    # always launched headless. Renders to the Xvfb :99 display started by
    # celery_worker's command in docker-compose.yml (see Dockerfile.backend's
    # ENV DISPLAY=:99). --disable-gpu is kept since there's no real GPU
    # device in the container even with Xvfb providing a display.
    _launch_viewport = viewport or _VIEWPORT
    process = subprocess.Popen(
        [
            chromium,
            f"--remote-debugging-port={_CDP_PORT}",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            f"--window-size={_launch_viewport['width']},{_launch_viewport['height']}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not _wait_for_cdp_sync(_CDP_PORT):
            logger.error("CDP at port %d did not become ready", _CDP_PORT)
            return {
                "status": "inconclusive",
                "summary": "Chromium CDP endpoint did not respond within timeout.",
                "events": [],
                "failing_step": None,
            }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(runner(cdp_url))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    finally:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()


def run_ai_test_sync(
    goal: str,
    environment_url: str = "about:blank",
    allowed_domains: Optional[list[str]] = None,
    sensitive_data: Optional[dict] = None,
    max_steps: int = 100,
    max_duration_s: Optional[int] = 600,
    on_event: Optional[Callable[[dict], None]] = None,
    llm_override: Optional[object] = None,
    cookies: Optional[list[dict]] = None,
    allow_unrestricted_domains: bool = False,
    run_id: Optional[str] = None,
    enable_live_capture: bool = False,
    on_video_ready: Optional[Callable[[str], None]] = None,
    viewport: Optional[dict] = None,
) -> dict:
    """
    Synchronous entry point for the Celery task.

    Launches Chromium, waits for CDP, runs async execution, tears down.
    Returns a result dict: {status, summary, events, failing_step}.

    max_steps (default 100) is a ceiling against runaway/looping goals, not
    the primary limiter for legitimate multi-action tasks. max_duration_s
    (default 600 = 10 minutes) is the real wall-clock safety backstop,
    tracked independently of step count. Pass max_duration_s=None to disable
    that backstop entirely (see resolve_with_ai's docstring) — callers that
    omit both keep today's 100-step/600s behavior unchanged.

    on_event: optional callback fired for every step event as it's created
    or updated, so the caller can persist it immediately (see
    app/workers/tasks/ai_execution.py) and make the SSE stream genuinely
    live instead of only reflecting the final bulk result.

    llm_override: passed straight through to resolve_with_ai() — see its
    docstring. None (the default) preserves today's fixed provider
    precedence for every existing caller.

    cookies: passed straight through to _execute_steps() — see its
    docstring. None (the default) preserves today's behavior for every
    existing caller.

    allow_unrestricted_domains: passed straight through to resolve_with_ai()
    — see its docstring. Default False preserves today's mandatory
    allowed_domains-when-sensitive_data behavior for every existing caller.

    run_id/enable_live_capture/on_video_ready: passed straight through to
    _execute_steps() — see its docstring. enable_live_capture defaults to
    False, preserving today's per-step-screenshot behavior for every
    existing caller (e.g. orchestrator.py's Hands step); only
    app/workers/tasks/ai_execution.py's New Vibe Test / Skill Replay
    Celery tasks opt in.

    viewport: {"width": int, "height": int} — see VIEWPORT_PRESETS. None
    (every existing caller) preserves today's fixed desktop _VIEWPORT for
    both the outer Chromium window (_run_with_chromium) and the inner page
    (_execute_steps/resolve_with_ai) — passed to both so they always agree.
    """

    def _runner(cdp_url: str):
        return _execute_steps(
            goal=goal,
            environment_url=environment_url,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            max_steps=max_steps,
            cdp_url=cdp_url,
            max_duration_s=max_duration_s,
            on_event=on_event,
            llm_override=llm_override,
            cookies=cookies,
            allow_unrestricted_domains=allow_unrestricted_domains,
            run_id=run_id,
            enable_live_capture=enable_live_capture,
            on_video_ready=on_video_ready,
            viewport=viewport,
        )

    return _run_with_chromium(_runner, viewport=viewport)


# ── Narrative summary (post-run, single LLM call) ────────────────────────────

def generate_narrative_summary(
    goal: str,
    status: str,
    events: list[dict],
    raw_summary: str | None,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """Produce a human-readable narrative of what the run tested and found.

    One LLM call through app.services.llm_router.complete() (which already
    has a primary→fallback chain and retries). Returns None on any failure
    so the caller falls back to the raw agent summary — a missing narrative
    must never fail run persistence.

    Step sampling (New Vibe Test Phase 3, 2026-07-28): previously always
    the first 60 steps, so a failure near the end of a long run (the most
    common place a multi-page functional flow actually breaks) was
    invisible to this summary. Now uses step_sampling.sample_steps — first
    10 + last 30 + every failed/anomalous step, up to the same 100-step
    budget the eval side (ai_eval.py) also uses — and logs a visible
    truncation marker (with run_id) when the sample is incomplete, instead
    of silently proceeding as if the summary saw everything.

    run_id: for the truncation log line only (see above) — optional and
    purely cosmetic, every existing caller that omits it keeps working
    identically, just without a run id in that one log line.
    """
    try:
        from app.services.llm_router import complete
        from app.services.step_sampling import sample_steps, truncation_marker

        sampled, was_truncated = sample_steps(events)
        if was_truncated:
            logger.warning(
                "Narrative summary for run_id=%s: %s",
                run_id, truncation_marker(len(events), len(sampled)),
            )

        lines = []
        if was_truncated:
            lines.append(truncation_marker(len(events), len(sampled)))
        for ev in sampled:
            desc = (ev.get("description") or "").strip().replace("\n", " ")
            lines.append(
                f"{ev.get('sequence')}. [{ev.get('status')}] {desc[:300]}"
            )
        steps_block = "\n".join(lines) or "(no steps recorded)"

        prompt = (
            f"Test goal:\n{goal}\n\n"
            f"Final status: {status}\n\n"
            f"Executed steps (sequence, status, description):\n{steps_block}\n\n"
            f"Agent's own closing note: {raw_summary or '(none)'}\n"
        )
        system = (
            "You are a QA reporting assistant. Given an automated browser test "
            "run (goal, executed steps, final status), write a concise summary "
            "for a QA engineer: what was tested, what the agent actually did, "
            "what was verified, and — if the run failed — where and why it "
            "failed. 2 short paragraphs maximum, plain prose, no headings, no "
            "bullet lists, no restating the raw step log."
        )
        result = complete(prompt, system=system, max_tokens=1024, temperature=0.2)
        text = (result.text or "").strip()
        return text or None
    except Exception as exc:
        logger.warning("Narrative summary generation failed (keeping raw summary): %s", exc)
        return None


# ── Skill replay (no LLM planning) ───────────────────────────────────────────

async def _replay_history(
    cdp_url: str,
    goal: str,
    history_json: str,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict],
    max_duration_s: int,
    on_step: Optional[Callable[[str, bool, Optional[str]], None]] = None,
    allow_unrestricted_domains: bool = False,
) -> dict:
    """Re-execute a stored browser-use AgentHistoryList against the live page.

    Uses Agent.rerun_history() (browser-use 0.1.40) which replays the stored
    actions by matching DOM state — no LLM planning calls happen per step
    (the Agent constructor still requires an llm instance, unused during
    rerun). skip_failures=False so a step that no longer matches fails the
    replay deterministically instead of being silently skipped.

    on_step(description, ok, error) is invoked once per replayed step after
    the rerun completes (rerun_history exposes no live hook in 0.1.40).

    allowed_domains required when sensitive_data present (same gate as
    resolve_with_ai — replay still drives a real browser with real
    credentials, so the same credential-leak containment applies), unless
    allow_unrestricted_domains=True — see resolve_with_ai()'s docstring for
    what that opts into and why.

    Returns {"success": bool, "action_summary": str, "duration_ms": int}.
    """
    from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig
    from browser_use.agent.views import AgentHistoryList

    # Safety gate — mirrors resolve_with_ai(). See the 2026-07-09 fix note
    # below: this parameter previously wasn't even accepted by this function.
    if sensitive_data and not allowed_domains and not allow_unrestricted_domains:
        raise ValueError(
            "allowed_domains must be provided when sensitive_data is set. "
            "Omitting it risks credential leakage to out-of-scope domains."
        )

    try:
        llm = _build_llm()
    except RuntimeError as exc:
        # browser-use requires an llm at construction time even though rerun
        # never invokes it for planning.
        return {
            "success": False,
            "action_summary": f"Skill replay unavailable: {exc}",
            "duration_ms": 0,
        }

    try:
        history = AgentHistoryList.model_validate_json(history_json)
    except Exception as exc:
        return {
            "success": False,
            "action_summary": f"Stored skill history is invalid: {exc}",
            "duration_ms": 0,
        }

    browser = Browser(
        config=BrowserConfig(
            cdp_url=cdp_url,
            disable_security=True,
            new_context_config=BrowserContextConfig(
                browser_window_size=_VIEWPORT,
                no_viewport=False,
                # BUG FIX (2026-07-09): see the matching fix + comment in
                # resolve_with_ai() above -- allowed_domains was accepted and
                # gated on but never forwarded to browser_use.
                allowed_domains=allowed_domains,
            ),
        )
    )
    agent_kwargs: dict = {"task": goal, "llm": llm, "browser": browser}
    if sensitive_data:
        agent_kwargs["sensitive_data"] = sensitive_data
        agent_kwargs["use_vision"] = False
    agent = Agent(**agent_kwargs)

    def _describe(idx: int) -> str:
        try:
            item = history.history[idx]
            return (
                item.model_output.current_state.next_goal
                or f"Replay step {idx + 1}"
            )
        except Exception:
            return f"Replay step {idx + 1}"

    start = time.monotonic()
    try:
        results = await asyncio.wait_for(
            agent.rerun_history(
                history, max_retries=2, skip_failures=False,
                delay_between_actions=1.0,
            ),
            timeout=float(max_duration_s),
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        first_error = None
        for idx, res in enumerate(results or []):
            err = getattr(res, "error", None)
            ok = not err
            if on_step is not None:
                try:
                    on_step(_describe(idx), ok, err)
                except Exception:
                    logger.exception("Replay step callback failed at step %d", idx)
            if err and first_error is None:
                first_error = (idx, err)

        if first_error is not None:
            idx, err = first_error
            return {
                "success": False,
                "action_summary": f"Replay failed at step {idx + 1}: {err}",
                "duration_ms": duration_ms,
            }
        return {
            "success": True,
            "action_summary": (
                f"Replayed {len(results or [])} recorded steps successfully "
                "without LLM planning."
            ),
            "duration_ms": duration_ms,
        }
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "action_summary": f"Skill replay timed out after {max_duration_s}s",
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"success": False, "action_summary": str(exc), "duration_ms": duration_ms}


async def _execute_replay_steps(
    goal: str,
    history_json: str,
    environment_url: str,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict],
    cdp_url: str,
    max_duration_s: int,
    on_event: Optional[Callable[[dict], None]],
    allow_ai_fallback: bool,
    cookies: Optional[list[dict]] = None,
    allow_unrestricted_domains: bool = False,
    run_id: Optional[str] = None,
    enable_live_capture: bool = False,
    on_video_ready: Optional[Callable[[str], None]] = None,
) -> dict:
    """Replay flow mirroring _execute_steps: deterministic nav step, stored
    history replay, deterministic final capture. Emits the same event shape
    so live persistence / SSE / result views work unchanged.

    cookies: see _execute_steps' docstring. Required for replaying a skill
    that was originally recorded from a kind="bypass" credential-profile run
    — that recorded history has no login-form steps in it (the agent started
    already authenticated), so replaying without re-injecting the cookie
    means it opens on a real login page it has no recorded actions for and
    fails immediately.

    allow_unrestricted_domains: passed through to both _replay_history() and
    the AI-fallback resolve_with_ai() call below — see resolve_with_ai()'s
    docstring.

    run_id/enable_live_capture/on_video_ready: see _execute_steps' docstring
    — same live CDP screencast + video capture, same default-False
    backward-compat guarantee.
    """
    from playwright.async_api import async_playwright

    events: list[dict] = []
    start_ts = time.monotonic()
    seq_counter = {"n": 0}

    def elapsed_ms() -> int:
        return int((time.monotonic() - start_ts) * 1000)

    def _notify(ev: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(dict(ev))
        except Exception:
            logger.exception("on_event callback failed for step %s", ev.get("sequence"))

    def _emit(step_type, description, status="running", screenshot_url=None, is_failing=False) -> dict:
        seq_counter["n"] += 1
        ev = {
            "sequence": seq_counter["n"],
            "status": status,
            "description": description,
            "step_type": step_type,
            "elapsed_ms": elapsed_ms(),
            "screenshot_url": screenshot_url,
            "highlighted_element": None,
            "is_failing_step": is_failing,
        }
        events.append(ev)
        _notify(ev)
        return ev

    def _update(ev: dict, **changes) -> None:
        ev.update(changes)
        _notify(ev)

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context(viewport=_VIEWPORT)
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        try:
            await page.set_viewport_size(_VIEWPORT)
        except Exception:
            logger.warning("Failed to set viewport size to %s", _VIEWPORT, exc_info=True)

        # See the matching block in _execute_steps — same live view/video
        # capture, same _end_live_capture cleanup before every browser.close().
        capture_session = None
        if enable_live_capture and run_id:
            from app.services.ai_run_capture import start_capture

            capture_session = await start_capture(page, run_id)

        async def _shot() -> Optional[str]:
            if enable_live_capture:
                return None
            try:
                shot = await page.screenshot()
                return "data:image/png;base64," + base64.b64encode(shot).decode()
            except Exception:
                logger.warning(
                    "Failed to capture step screenshot during skill replay "
                    "(run_id=%s).",
                    run_id, exc_info=True,
                )
                return None

        # ── Step: inject bypass auth cookie (kind="bypass" profiles only) ──
        if cookies:
            cookie_event = _emit("deterministic", "Inject authenticated session cookie")
            try:
                await context.add_cookies(cookies)
                _update(cookie_event, status="passed", elapsed_ms=elapsed_ms())
            except Exception as exc:
                logger.exception("Failed to inject bypass auth cookie(s) during replay: %s", exc)
                _update(
                    cookie_event, status="failed", elapsed_ms=elapsed_ms(), is_failing_step=True
                )
                await _end_live_capture(capture_session, on_video_ready)
                await browser.close()
                return {
                    "status": "failed",
                    "summary": f"Step {cookie_event['sequence']} failed: {exc}",
                    "events": events,
                    "failing_step": cookie_event,
                    "history_json": None,
                }

        # ── Step: deterministic navigation ──────────────────────────────
        nav_event = _emit("deterministic", "Launch browser and navigate to application")
        try:
            if environment_url and environment_url != "about:blank":
                await page.goto(environment_url, wait_until="domcontentloaded", timeout=30000)
            _update(nav_event, status="passed", elapsed_ms=elapsed_ms(), screenshot_url=await _shot())
        except Exception as exc:
            logger.exception("Replay navigation step failed: %s", exc)
            _update(
                nav_event, status="failed", elapsed_ms=elapsed_ms(),
                screenshot_url=await _shot(), is_failing_step=True,
            )
            await _end_live_capture(capture_session, on_video_ready)
            await browser.close()
            return {
                "status": "failed",
                "summary": f"Step {nav_event['sequence']} failed: {exc}",
                "events": events,
                "failing_step": nav_event,
                "history_json": None,
            }

        # ── Step(s): replay stored actions (no LLM planning) ────────────
        def _on_replay_step(description: str, ok: bool, error: Optional[str]) -> None:
            _emit(
                "ai_scoped",
                description if ok else f"{description} — {error}",
                status="passed" if ok else "failed",
                is_failing=not ok,
            )

        replay_result = await _replay_history(
            cdp_url=cdp_url,
            goal=goal,
            history_json=history_json,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            max_duration_s=max_duration_s,
            on_step=_on_replay_step,
            allow_unrestricted_domains=allow_unrestricted_domains,
        )

        used_fallback = False
        if not replay_result["success"] and allow_ai_fallback:
            used_fallback = True
            _emit(
                "deterministic",
                "Replay failed — falling back to full AI planning for the original goal",
                status="passed",
            )

            # Same decision -> execution step lifecycle as _execute_steps
            # above; see the comment there for why "passed"-at-decision-time
            # was wrong.
            pending_step: dict = {"event": None}

            def _resolve_pending_step(ok: bool = True, error: Optional[str] = None) -> None:
                ev = pending_step["event"]
                if ev is None:
                    return
                pending_step["event"] = None
                changes = {"status": "passed" if ok else "failed", "elapsed_ms": elapsed_ms()}
                if not ok and error:
                    changes["description"] = f"{ev['description']} — {error}"
                _update(ev, **changes)

            def _on_agent_step(description: str, screenshot_b64: Optional[str]) -> None:
                _resolve_pending_step(ok=True)
                shot_url = (
                    f"data:image/png;base64,{screenshot_b64}"
                    if screenshot_b64 and not enable_live_capture
                    else None
                )
                pending_step["event"] = _emit(
                    "ai_scoped", description, status="running", screenshot_url=shot_url
                )

            def _on_agent_step_complete(ok: bool, error: Optional[str]) -> None:
                _resolve_pending_step(ok=ok, error=error)

            replay_result = await resolve_with_ai(
                cdp_url=cdp_url,
                task=goal,
                allowed_domains=allowed_domains,
                sensitive_data=sensitive_data,
                max_duration_s=max_duration_s,
                on_step=_on_agent_step,
                allow_unrestricted_domains=allow_unrestricted_domains,
                on_step_complete=_on_agent_step_complete,
            )
            _resolve_pending_step(ok=bool(replay_result.get("success")))

        if not replay_result["success"]:
            fail_event = _emit(
                "ai_scoped",
                replay_result.get("action_summary", "Skill replay failed."),
                status="failed",
                screenshot_url=await _shot(),
                is_failing=True,
            )
            await _end_live_capture(capture_session, on_video_ready)
            await browser.close()
            return {
                "status": "failed",
                "summary": replay_result.get("action_summary", "Skill replay failed."),
                "events": events,
                "failing_step": fail_event,
                "history_json": None,
            }

        # ── Step: deterministic final capture ───────────────────────────
        verify_event = _emit("deterministic", "Capture final state and evaluate outcome")
        _update(verify_event, status="passed", elapsed_ms=elapsed_ms(), screenshot_url=await _shot())
        await _end_live_capture(capture_session, on_video_ready)
        await browser.close()

    summary = replay_result.get("action_summary") or "Skill replay completed."
    if used_fallback:
        summary = f"[AI fallback after failed replay] {summary}"
    return {
        "status": "passed",
        "summary": summary,
        "events": events,
        "failing_step": None,
        "history_json": replay_result.get("history_json"),
    }


def run_skill_replay_sync(
    goal: str,
    history_json: str,
    environment_url: str = "about:blank",
    allowed_domains: Optional[list[str]] = None,
    sensitive_data: Optional[dict] = None,
    max_duration_s: int = 600,
    on_event: Optional[Callable[[dict], None]] = None,
    allow_ai_fallback: bool = False,
    cookies: Optional[list[dict]] = None,
    allow_unrestricted_domains: bool = False,
    run_id: Optional[str] = None,
    enable_live_capture: bool = False,
    on_video_ready: Optional[Callable[[str], None]] = None,
) -> dict:
    """Synchronous entry point for the skill replay Celery task.

    Same Chromium/CDP lifecycle and result shape as run_ai_test_sync:
    {status, summary, events, failing_step, history_json}. A failed replay
    marks the run failed; there is no silent fallback to AI planning unless
    allow_ai_fallback=True is explicitly passed.

    cookies: see _execute_replay_steps' docstring — required to replay a
    skill recorded from a kind="bypass" credential-profile run.

    allow_unrestricted_domains: passed straight through to
    _execute_replay_steps() — see resolve_with_ai()'s docstring. Default
    False preserves today's behavior for every existing caller.

    run_id/enable_live_capture/on_video_ready: passed straight through to
    _execute_replay_steps() — see _execute_steps' docstring. Default False
    preserves today's per-step-screenshot behavior for every existing caller.
    """
    try:
        import browser_use  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        return {
            "status": "inconclusive",
            "summary": f"Execution engine unavailable: {exc}",
            "events": [],
            "failing_step": None,
        }

    def _runner(cdp_url: str):
        return _execute_replay_steps(
            goal=goal,
            history_json=history_json,
            environment_url=environment_url,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            cdp_url=cdp_url,
            max_duration_s=max_duration_s,
            on_event=on_event,
            allow_ai_fallback=allow_ai_fallback,
            cookies=cookies,
            allow_unrestricted_domains=allow_unrestricted_domains,
            run_id=run_id,
            enable_live_capture=enable_live_capture,
            on_video_ready=on_video_ready,
        )

    return _run_with_chromium(_runner)
