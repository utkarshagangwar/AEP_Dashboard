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
    """Chain multiple same-provider LLM clients with LangChain's native
    .with_fallbacks(): if the active key raises a rate-limit error mid-call,
    LangChain transparently retries the exact same call on the next key
    instead of failing the step. This also correctly propagates through
    browser-use's later `.with_structured_output(...)` call, since
    RunnableWithFallbacks forwards runnable-returning methods to every
    client in the chain.

    Single-key case (the common one) just returns that client unchanged —
    no behavior change for anyone with only one key configured.
    """
    if len(clients) <= 1:
        return clients[0]
    primary, *rest = clients
    return primary.with_fallbacks(rest, exceptions_to_handle=rate_limit_exceptions)


def _build_llm():
    """
    Build a langchain LLM for the browser-use Agent, with same-provider key
    rotation via LangChain's native .with_fallbacks() (see
    _with_key_rotation above).

    Provider precedence is unchanged from before:
      1. Anthropic → ChatAnthropic          (claude-3-5-haiku-20241022 by default)
      2. OpenAI    → ChatOpenAI             (gpt-4o-mini by default)
      3. Google    → ChatGoogleGenerativeAI (gemini-3.5-flash by default)

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

    anthropic_keys = _get_key_list("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
    if anthropic_keys:
        from anthropic import RateLimitError as AnthropicRateLimitError
        from langchain_anthropic import ChatAnthropic

        model = model_override or "claude-3-5-haiku-20241022"
        logger.info(
            "AI runner: using Anthropic model %s (%d key%s configured)",
            model,
            len(anthropic_keys),
            "" if len(anthropic_keys) == 1 else "s",
        )
        clients = [ChatAnthropic(model=model, api_key=k) for k in anthropic_keys]
        return _with_key_rotation(clients, (AnthropicRateLimitError,))

    openai_keys = _get_key_list("OPENAI_API_KEYS", "OPENAI_API_KEY")
    if openai_keys:
        from openai import RateLimitError as OpenAIRateLimitError
        from langchain_openai import ChatOpenAI

        model = model_override or "gpt-4o-mini"
        logger.info(
            "AI runner: using OpenAI model %s (%d key%s configured)",
            model,
            len(openai_keys),
            "" if len(openai_keys) == 1 else "s",
        )
        clients = [ChatOpenAI(model=model, api_key=k) for k in openai_keys]
        return _with_key_rotation(clients, (OpenAIRateLimitError,))

    google_keys = _get_key_list("GOOGLE_API_KEYS", "GOOGLE_API_KEY")
    if google_keys:
        from google.api_core.exceptions import ResourceExhausted
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = model_override or "gemini-3.5-flash"
        logger.info(
            "AI runner: using Google Gemini model %s (%d key%s configured)",
            model,
            len(google_keys),
            "" if len(google_keys) == 1 else "s",
        )
        # Pinned to the langchain-google-genai 2.x line (not 4.x) because
        # browser-use==0.1.40 hard-pins langchain-anthropic==0.3.3, which
        # requires langchain-core<0.4.0 — incompatible with 4.x's
        # langchain-core>=1.0.0 requirement. 2.x still uses google_api_key=
        # and talks to the classic generateContent REST endpoint, which
        # accepts new model IDs like gemini-3.5-flash without an SDK bump.
        clients = [
            ChatGoogleGenerativeAI(model=model, google_api_key=k) for k in google_keys
        ]
        return _with_key_rotation(clients, (ResourceExhausted,))

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
                browser_window_size=_VIEWPORT, no_viewport=False
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

        if success:
            summary = result.final_result() or "Agent completed the goal."
        elif result is not None and result.has_errors():
            errors = [e for e in result.errors() if e]
            summary = f"Agent failed: {errors[-1]}" if errors else "Agent encountered an error."
        elif is_successful is None:
            summary = "Agent did not finish the goal within max_steps."
        else:
            summary = result.final_result() or "Agent did not complete the goal."

        return {"success": success, "action_summary": summary, "duration_ms": duration_ms}
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
    }


def run_ai_test_sync(
    goal: str,
    environment_url: str = "about:blank",
    allowed_domains: Optional[list[str]] = None,
    sensitive_data: Optional[dict] = None,
    max_steps: int = 30,
    max_duration_s: int = 600,
    on_event: Optional[Callable[[dict], None]] = None,
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
            result = loop.run_until_complete(
                _execute_steps(
                    goal=goal,
                    environment_url=environment_url,
                    allowed_domains=allowed_domains,
                    sensitive_data=sensitive_data,
                    max_steps=max_steps,
                    cdp_url=cdp_url,
                    max_duration_s=max_duration_s,
                    on_event=on_event,
                )
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        return result

    finally:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()
