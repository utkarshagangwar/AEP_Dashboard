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
import time
import urllib.request
import urllib.error
from typing import Callable, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_CDP_PORT = 9222
_CDP_TIMEOUT_S = 15

# Desktop viewport for AI test runs. Without an explicit window size,
# headless Chromium falls back to a small default viewport, which makes
# target sites render their mobile/responsive layout (hamburger menus,
# collapsed nav, etc.) instead of the desktop UI testers expect to see.
_VIEWPORT = {"width": 1530, "height": 820}


# ── CDP helpers ──────────────────────────────────────────────────────────────

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


def _with_key_rotation(
    clients: list, rate_limit_exceptions: tuple[type[BaseException], ...]
):
    """Same-provider key rotation is NOT usable for the browser-use Agent path.

    This previously wrapped multiple clients with LangChain's native
    .with_fallbacks(), which returns a RunnableWithFallbacks — but that is
    not a BaseChatModel instance, and browser_use.Agent.__init__ defaults its
    AgentSettings.page_extraction_llm/planner_llm fields (Pydantic, typed
    strictly as Optional[BaseChatModel]) from the llm kwarg passed in. Passing
    a RunnableWithFallbacks there crashes Agent construction itself:
    "AttributeError: 'ChatGoogleGenerativeAI' object has no attribute 'get'"
    (Pydantic attempting to validate/coerce the wrapper as if it were the
    wrapped model's own init data). Confirmed by direct reproduction against
    the actual installed browser-use/langchain-core versions.

    Until browser-use accepts a plain Runnable (or this project moves off
    the with_fallbacks() approach entirely, e.g. explicit per-call retry
    against a rotating key list), always use a single client — browser-use's
    own step-level retry (max_failures) still provides resilience, just not
    literal cross-key rotation. Single-key case is unaffected either way.
    """
    return clients[0]


def _anthropic_client(model: str, keys: list[str]):
    """Build a (possibly key-rotating) ChatAnthropic client.

    Extracted from _build_llm() so the orchestrator's model_pool.py can build
    an explicit-choice client through the same construction logic instead of
    duplicating it — behavior-preserving, _build_llm() below just delegates
    here for its Anthropic branch.
    """
    from anthropic import RateLimitError as AnthropicRateLimitError
    from langchain_anthropic import ChatAnthropic

    logger.info(
        "AI runner: using Anthropic model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )
    clients = [ChatAnthropic(model=model, api_key=k) for k in keys]
    return _with_key_rotation(clients, (AnthropicRateLimitError,))


def _openai_client(model: str, keys: list[str]):
    """Build a (possibly key-rotating) ChatOpenAI client. See _anthropic_client."""
    from openai import RateLimitError as OpenAIRateLimitError
    from langchain_openai import ChatOpenAI

    logger.info(
        "AI runner: using OpenAI model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )
    clients = [ChatOpenAI(model=model, api_key=k) for k in keys]
    return _with_key_rotation(clients, (OpenAIRateLimitError,))


def _google_client(model: str, keys: list[str]):
    """Build a per-request key-rotating ChatGoogleGenerativeAI client.

    Pinned to the langchain-google-genai 2.x line (not 4.x) because
    browser-use==0.1.40 hard-pins langchain-anthropic==0.3.3, which
    requires langchain-core<0.4.0 — incompatible with 4.x's
    langchain-core>=1.0.0 requirement. 2.x still uses google_api_key=
    and talks to the classic generateContent REST endpoint, which
    accepts new model IDs without an SDK bump.

    Unlike _with_key_rotation (which can't wrap clients for browser-use —
    see its docstring), rotation here is done by SUBCLASSING
    ChatGoogleGenerativeAI, so the returned object still IS a
    BaseChatModel and passes browser-use's strict pydantic validation.
    On a rate-limit error (429/ResourceExhausted) mid-run, the call is
    retried on each remaining key before failing — free-tier Gemini
    quotas are per-key per-project, so a multi-key setup keeps a long
    agent run alive when one key runs dry partway through.

    max_retries=2 (default 6) so a throttled key fails over to the next
    key in seconds instead of stalling the run in exponential backoff.
    """
    from google.api_core.exceptions import ResourceExhausted
    from langchain_google_genai import ChatGoogleGenerativeAI

    logger.info(
        "AI runner: using Google Gemini model %s (%d key%s configured)",
        model,
        len(keys),
        "" if len(keys) == 1 else "s",
    )

    if len(keys) == 1:
        return ChatGoogleGenerativeAI(model=model, google_api_key=keys[0])

    class _RotatingGoogleChat(ChatGoogleGenerativeAI):
        """ChatGoogleGenerativeAI that retries rate-limited calls on
        sibling clients (other API keys) before giving up."""

        def _set_pool(self, pool: list) -> None:
            # Bypass pydantic field validation for the private pool ref.
            object.__setattr__(self, "_pool", pool)

        def _generate(self, *args, **kwargs):
            last_exc = None
            for i, client in enumerate(getattr(self, "_pool", [self])):
                try:
                    return ChatGoogleGenerativeAI._generate(client, *args, **kwargs)
                except ResourceExhausted as exc:
                    last_exc = exc
                    logger.warning(
                        "Google key %d rate-limited mid-run — rotating to next key.",
                        i + 1,
                    )
            raise last_exc

        async def _agenerate(self, *args, **kwargs):
            last_exc = None
            for i, client in enumerate(getattr(self, "_pool", [self])):
                try:
                    return await ChatGoogleGenerativeAI._agenerate(
                        client, *args, **kwargs
                    )
                except ResourceExhausted as exc:
                    last_exc = exc
                    logger.warning(
                        "Google key %d rate-limited mid-run — rotating to next key.",
                        i + 1,
                    )
            raise last_exc

    clients = [
        _RotatingGoogleChat(model=model, google_api_key=k, max_retries=2)
        for k in keys
    ]
    for c in clients:
        c._set_pool(clients)
    return clients[0]


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


def _google_key_valid(key: str) -> bool:
    return _key_probe(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        {},
    )


def _google_pick_working_keys(model: str, keys: list[str]) -> list[str]:
    """Reorder Google keys so a key that can actually serve `model` right now
    comes first. Free-tier Gemini quotas are per-key per-model per-day, so
    the first configured key being exhausted (429) used to fail the whole
    run even when other keys had quota left — _with_key_rotation only ever
    uses clients[0] (browser-use constraint, see above).

    Each candidate is probed with a 1-token generateContent call. Keys that
    return 429/401/403 are moved to the back; any other outcome (success,
    network error) accepts the key (fail-open). Returns [] when every key
    is exhausted so the caller can try a different model instead."""
    import json as _json

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
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
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
    if not _google_key_valid(google_keys[0]):
        logger.warning(
            "GOOGLE_API_KEY(S) configured but rejected by the API (invalid key)."
        )
        return None

    model_prefs = [model_override] if model_override else _GOOGLE_MODEL_PREFS
    for google_model in model_prefs:
        usable_keys = _google_pick_working_keys(google_model, google_keys)
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
    Build a langchain LLM for the browser-use Agent, with same-provider key
    rotation via LangChain's native .with_fallbacks() (see
    _with_key_rotation above).

    Provider precedence is unchanged from before:
      1. Anthropic → ChatAnthropic          (claude-3-5-haiku-20241022 by default)
      2. OpenAI    → ChatOpenAI             (gpt-4o-mini by default)
      3. Google    → ChatGoogleGenerativeAI (gemini-2.5-flash-lite by default)

    Within whichever provider is configured, supply multiple keys as a
    comma-separated list (ANTHROPIC_API_KEYS / OPENAI_API_KEYS /
    GOOGLE_API_KEYS) to rotate across them when one hits a rate limit.
    The original single-key env vars (ANTHROPIC_API_KEY etc.) still work
    unchanged for a single-key setup.

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

    anthropic_keys = _get_key_list("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
    if anthropic_keys:
        any_configured = True
        if _anthropic_key_valid(anthropic_keys[0]):
            return _anthropic_client(
                model_override or "claude-3-5-haiku-20241022", anthropic_keys
            )
        logger.warning(
            "ANTHROPIC_API_KEY(S) configured but rejected by the API (invalid "
            "key) — skipping Anthropic and trying the next provider."
        )

    openai_keys = _get_key_list("OPENAI_API_KEYS", "OPENAI_API_KEY")
    if openai_keys:
        any_configured = True
        if _openai_key_valid(openai_keys[0]):
            return _openai_client(model_override or "gpt-4o-mini", openai_keys)
        logger.warning(
            "OPENAI_API_KEY(S) configured but rejected by the API (invalid "
            "key) — skipping OpenAI and trying the next provider."
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

    if any_configured:
        raise RuntimeError(
            "LLM API key(s) are configured but all were rejected by their "
            "providers (invalid/expired keys). Update ANTHROPIC_API_KEY(S), "
            "OPENAI_API_KEY(S), or GOOGLE_API_KEY(S) in your .env file."
        )
    raise RuntimeError(
        "No LLM API key configured. Set one of ANTHROPIC_API_KEY(S), "
        "OPENAI_API_KEY(S), or GOOGLE_API_KEY(S) in your .env file to enable "
        "AI test execution."
    )


async def resolve_with_ai(
    cdp_url: str,
    task: str,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict] = None,
    max_steps: int = 30,
    max_duration_s: int = 600,
    on_step: Optional[Callable[[str, Optional[str]], None]] = None,
    llm_override: Optional[object] = None,
) -> dict:
    """
    Scoped browser-use agent (Phase 2.3) — browser_use 0.1.40 API.

    Safety invariants:
    - max_steps (default 30) is a ceiling against runaway/looping goals, not
      meant to be the primary limiter for legitimate multi-action tasks —
      raised from an earlier default of 5, which was cutting off real
      multi-step goals before they could finish.
    - max_duration_s (default 600 = 10 minutes) is the actual wall-clock
      safety backstop: time is a better proxy than step count for "this
      goal is taking too long / costing too much", so it's now tracked
      independently of max_steps rather than derived from it.
    - use_vision=False when sensitive_data present (prevents credential leakage to LLM)
    - allowed_domains required when sensitive_data present

    on_step: optional callback invoked synchronously after each internal agent
    action is decided (before it executes), so callers can surface live,
    granular progress instead of only seeing the aggregate pass/fail result
    once the whole goal finishes. Receives (description, screenshot_b64).
    Exceptions raised by the callback are caught and logged — they must
    never abort the underlying browser automation.

    llm_override: an already-built LangChain BaseChatModel (e.g. from
    app.services.model_pool.to_langchain_client()) to use instead of the
    default Anthropic->OpenAI->Google precedence in _build_llm(). Used by
    the orchestrator to steer which model drives this run; every existing
    caller omits this and gets identical behavior to before.

    Returns: {"success": bool, "action_summary": str, "duration_ms": int}
    """
    # Safety gate
    if sensitive_data and not allowed_domains:
        raise ValueError(
            "allowed_domains must be provided when sensitive_data is set. "
            "Omitting it risks credential leakage to out-of-scope domains."
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
    browser = Browser(
        config=BrowserConfig(
            cdp_url=cdp_url,
            disable_security=True,
            new_context_config=BrowserContextConfig(
                browser_window_size=_VIEWPORT,
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
    }
    if sensitive_data:
        agent_kwargs["sensitive_data"] = sensitive_data
        agent_kwargs["use_vision"] = False

    if on_step is not None:

        async def _handle_new_step(state, model_output, n_steps) -> None:
            """browser-use calls this after each internal step is decided
            (before the action executes) — this is what makes step
            visibility genuinely live instead of only known at the end."""
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

    agent = Agent(**agent_kwargs)

    timeout_s = max_duration_s
    try:
        # max_steps is a run()-level parameter in browser_use 0.1.40
        result = await asyncio.wait_for(
            agent.run(max_steps=max_steps), timeout=float(timeout_s)
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
    max_duration_s: int = 600,
    on_event: Optional[Callable[[dict], None]] = None,
    llm_override: Optional[object] = None,
    cookies: Optional[list[dict]] = None,
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

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        contexts = browser.contexts
        context = (
            contexts[0] if contexts else await browser.new_context(viewport=_VIEWPORT)
        )
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # Force the desktop viewport regardless of how the context/page was
        # created. CDP-attached Chromium can already have a default context
        # with no explicit viewport, which renders sites in a small/
        # responsive layout instead of the desktop size testers expect.
        try:
            await page.set_viewport_size(_VIEWPORT)
        except Exception:
            logger.warning("Failed to set viewport size to %s", _VIEWPORT, exc_info=True)

        # ── Step: inject bypass auth cookie (kind="bypass" profiles only) ──
        if cookies:
            cookie_event = _emit("deterministic", "Inject authenticated session cookie")
            try:
                await context.add_cookies(cookies)
                _update(cookie_event, status="passed", elapsed_ms=elapsed_ms())
            except Exception as exc:
                logger.exception("Failed to inject bypass auth cookie(s): %s", exc)
                _update(
                    cookie_event, status="failed", elapsed_ms=elapsed_ms(), is_failing_step=True
                )
                await browser.close()
                return {
                    "status": "failed",
                    "summary": f"Step {cookie_event['sequence']} failed: {exc}",
                    "events": events,
                    "failing_step": cookie_event,
                }

        # ── Step: deterministic navigation ──────────────────────────────
        nav_event = _emit("deterministic", "Launch browser and navigate to application")
        try:
            if environment_url and environment_url != "about:blank":
                await page.goto(
                    environment_url, wait_until="domcontentloaded", timeout=30000
                )
            shot = await page.screenshot()
            _update(
                nav_event,
                status="passed",
                elapsed_ms=elapsed_ms(),
                screenshot_url="data:image/png;base64," + base64.b64encode(shot).decode(),
            )
        except Exception as exc:
            logger.exception("Navigation step failed: %s", exc)
            try:
                shot = await page.screenshot()
                b64 = "data:image/png;base64," + base64.b64encode(shot).decode()
            except Exception:
                b64 = None
            _update(
                nav_event,
                status="failed",
                elapsed_ms=elapsed_ms(),
                screenshot_url=b64,
                is_failing_step=True,
            )
            await browser.close()
            return {
                "status": "failed",
                "summary": f"Step {nav_event['sequence']} failed: {exc}",
                "events": events,
                "failing_step": nav_event,
            }

        # ── Step(s): AI-scoped goal — one live event per real agent action ──
        history_json: Optional[str] = None
        if browser_use_available:
            def _on_agent_step(description: str, screenshot_b64: Optional[str]) -> None:
                shot_url = (
                    f"data:image/png;base64,{screenshot_b64}" if screenshot_b64 else None
                )
                _emit("ai_scoped", description, status="passed", screenshot_url=shot_url)

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
                )
            except Exception as exc:
                logger.exception("AI-scoped execution raised unexpectedly: %s", exc)
                agent_result = {
                    "success": False,
                    "action_summary": str(exc),
                    "duration_ms": elapsed_ms(),
                }

            if not agent_result["success"]:
                try:
                    shot = await page.screenshot()
                    fail_shot_url = "data:image/png;base64," + base64.b64encode(shot).decode()
                except Exception:
                    fail_shot_url = None
                fail_event = _emit(
                    "ai_scoped",
                    agent_result.get(
                        "action_summary", "AI agent could not complete the goal."
                    ),
                    status="failed",
                    screenshot_url=fail_shot_url,
                    is_failing=True,
                )
                await browser.close()
                return {
                    "status": "failed",
                    "summary": agent_result.get(
                        "action_summary", "AI agent could not complete the goal."
                    ),
                    "events": events,
                    "failing_step": fail_event,
                }

            history_json = agent_result.get("history_json")
        else:
            # Deterministic fallback when browser-use is unavailable
            _emit(
                "ai_scoped",
                f"[browser-use not installed] {goal}",
                status="inconclusive",
            )
            await browser.close()
            return {
                "status": "inconclusive",
                "summary": (
                    "browser-use library is not installed. "
                    "Add browser-use to requirements.txt and rebuild the container."
                ),
                "events": events,
                "failing_step": None,
            }

        # ── Step: deterministic final capture ───────────────────────────
        verify_event = _emit("deterministic", "Capture final state and evaluate outcome")
        try:
            shot = await page.screenshot()
            _update(
                verify_event,
                status="passed",
                elapsed_ms=elapsed_ms(),
                screenshot_url="data:image/png;base64," + base64.b64encode(shot).decode(),
            )
        except Exception as exc:
            logger.exception("Final capture step failed: %s", exc)
            _update(
                verify_event,
                status="failed",
                elapsed_ms=elapsed_ms(),
                is_failing_step=True,
            )
            await browser.close()
            return {
                "status": "failed",
                "summary": f"Step {verify_event['sequence']} failed: {exc}",
                "events": events,
                "failing_step": verify_event,
            }

        await browser.close()

    return {
        "status": "passed",
        "summary": f"All {len(events)} steps completed successfully.",
        "events": events,
        "failing_step": None,
        "history_json": history_json,
    }


def _run_with_chromium(runner: Callable[[str], "asyncio.Future"]) -> dict:
    """Launch Chromium with CDP, run an async runner(cdp_url) coroutine
    factory in a fresh event loop, tear the browser down. Shared by
    run_ai_test_sync and run_skill_replay_sync."""
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
    process = subprocess.Popen(
        [
            chromium,
            f"--remote-debugging-port={_CDP_PORT}",
            "--no-sandbox",
            "--disable-gpu",
            "--headless",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            f"--window-size={_VIEWPORT['width']},{_VIEWPORT['height']}",
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
    max_steps: int = 30,
    max_duration_s: int = 600,
    on_event: Optional[Callable[[dict], None]] = None,
    llm_override: Optional[object] = None,
    cookies: Optional[list[dict]] = None,
) -> dict:
    """
    Synchronous entry point for the Celery task.

    Launches Chromium, waits for CDP, runs async execution, tears down.
    Returns a result dict: {status, summary, events, failing_step}.

    max_steps (default 30) is a ceiling against runaway/looping goals, not
    the primary limiter for legitimate multi-action tasks. max_duration_s
    (default 600 = 10 minutes) is the real wall-clock safety backstop,
    tracked independently of step count.

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
        )

    return _run_with_chromium(_runner)


# ── Narrative summary (post-run, single LLM call) ────────────────────────────

def generate_narrative_summary(
    goal: str,
    status: str,
    events: list[dict],
    raw_summary: str | None,
) -> Optional[str]:
    """Produce a human-readable narrative of what the run tested and found.

    One LLM call through app.services.llm_router.complete() (which already
    has a primary→fallback chain and retries). Returns None on any failure
    so the caller falls back to the raw agent summary — a missing narrative
    must never fail run persistence.
    """
    try:
        from app.services.llm_router import complete

        lines = []
        for ev in events:
            desc = (ev.get("description") or "").strip().replace("\n", " ")
            lines.append(
                f"{ev.get('sequence')}. [{ev.get('status')}] {desc[:300]}"
            )
        steps_block = "\n".join(lines[:60]) or "(no steps recorded)"

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
    credentials, so the same credential-leak containment applies).

    Returns {"success": bool, "action_summary": str, "duration_ms": int}.
    """
    from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig
    from browser_use.agent.views import AgentHistoryList

    # Safety gate — mirrors resolve_with_ai(). See the 2026-07-09 fix note
    # below: this parameter previously wasn't even accepted by this function.
    if sensitive_data and not allowed_domains:
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

        async def _shot() -> Optional[str]:
            try:
                shot = await page.screenshot()
                return "data:image/png;base64," + base64.b64encode(shot).decode()
            except Exception:
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
        )

        used_fallback = False
        if not replay_result["success"] and allow_ai_fallback:
            used_fallback = True
            _emit(
                "deterministic",
                "Replay failed — falling back to full AI planning for the original goal",
                status="passed",
            )

            def _on_agent_step(description: str, screenshot_b64: Optional[str]) -> None:
                shot_url = f"data:image/png;base64,{screenshot_b64}" if screenshot_b64 else None
                _emit("ai_scoped", description, status="passed", screenshot_url=shot_url)

            replay_result = await resolve_with_ai(
                cdp_url=cdp_url,
                task=goal,
                allowed_domains=allowed_domains,
                sensitive_data=sensitive_data,
                max_duration_s=max_duration_s,
                on_step=_on_agent_step,
            )

        if not replay_result["success"]:
            fail_event = _emit(
                "ai_scoped",
                replay_result.get("action_summary", "Skill replay failed."),
                status="failed",
                screenshot_url=await _shot(),
                is_failing=True,
            )
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
) -> dict:
    """Synchronous entry point for the skill replay Celery task.

    Same Chromium/CDP lifecycle and result shape as run_ai_test_sync:
    {status, summary, events, failing_step, history_json}. A failed replay
    marks the run failed; there is no silent fallback to AI planning unless
    allow_ai_fallback=True is explicitly passed.

    cookies: see _execute_replay_steps' docstring — required to replay a
    skill recorded from a kind="bypass" credential-profile run.
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
        )

    return _run_with_chromium(_runner)
