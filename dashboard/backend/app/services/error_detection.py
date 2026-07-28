"""In-run application-error detection for AI Vibe Test runs.

WHY THIS EXISTS (bug, 2026-07-28)
---------------------------------
A Functional Test "Happy Path" run contained the step "Verify AI-Generated
Questions feature is working or not". The agent clicked 'Generate with AI',
the app rendered "Error: Failed to start question generation", and the run
was still reported as PASSED.

That was not a prompt problem -- it was structural. Step pass/fail in
ai_runner.resolve_with_ai comes from exactly one place: browser-use's
`ActionResult.error` (see _tracked_multi_act). That field is a *mechanical*
signal -- element not found, click threw, navigation timed out. A click that
lands perfectly and then causes the application under test to render an
error banner produces NO ActionResult.error, so the step was marked passed.
Nothing anywhere in the pipeline read page content after an action, so the
error string never entered the step record, never reached the post-run GEval
judge (app.services.ai_eval.evaluate_run judges step *descriptions*, which
contained no error text), and was invisible to the expected-results visual
judge too (it only ever sees the FINAL screenshot, by which point a
transient toast is long gone).

This module closes that gap: after every real browser action, look at what
the application actually rendered and decide whether it just showed the user
an error.

DETECTION STRATEGY (two tiers, cheap-first)
-------------------------------------------
Tier 1 -- DOM scan (always, no LLM, ~1 evaluate() call per action):
    Requires TWO independent signals before flagging, which is what keeps
    false positives down:
      (a) the element carries error/alert semantics -- role="alert",
          role="alertdialog", aria-live="assertive", or an error/toast/
          snackbar/notification/danger class, id or data-testid; AND
      (b) its visible text matches a conservative "hard failure" vocabulary
          (error / failed / went wrong / unable to / timed out / ...).
    Either signal alone is far too noisy: a success toast also uses
    role="alert", and the word "error" appears in plenty of static copy.
    Together they are a strong indicator. "Error: Failed to start question
    generation" in a toast satisfies both.

    Deliberately NOT in the vocabulary: "invalid", "required", "denied",
    "not allowed". Those are overwhelmingly intentional form-validation
    messages, and a QA suite frequently asserts them on purpose -- flagging
    them would manufacture false failures on tests that are working exactly
    as designed. Also excluded, each for a concrete false positive found in
    review: "cannot"/"can't" (confirmation dialogs are usually
    role="alertdialog" and say "this action cannot be undone"), "timeout"
    (settings labels), "try again" (benign retry prompts). See the inline
    notes on HARD in _DOM_SCAN_JS. A BENIGN veto list additionally
    suppresses text where an error word is a label or a negation ("no errors
    found", "error rate", "error log").

    WHAT TIER 1 CANNOT SEE, BY DESIGN: a step that silently does nothing --
    the click registers, no error is rendered anywhere, and the record simply
    is not created. There is nothing on the page to detect. That failure mode
    is the job of the end-of-run expected-results judge
    (ai_eval.evaluate_expected_results), which compares a final screenshot
    against the test's own "Expected Results". These two mechanisms are
    complements, not substitutes.

Tier 2 -- LLM vision escalation (only when tier 1 is INCONCLUSIVE):
    "Inconclusive" has one precise meaning here: the DOM scan found nothing,
    but a real API call (xhr/fetch/document) came back 4xx/5xx since the last
    check. That is a strong hint the app broke while leaving no semantically
    marked-up error on screen -- exactly the class of failure tier 1 cannot
    see -- but it is also frequently benign (a 404 on an optional resource,
    an expected 401 probe). So we pay for one screenshot + one vision call to
    ask the only question that matters: does the user actually see an error?
    Hard-capped per run (AI_ERROR_DETECTION_VISION_MAX_CALLS, default 10) so
    a chatty app can never turn one test run into hundreds of LLM calls.

Console errors are recorded as *supporting evidence only* and can never on
their own flag a step. Real applications log console errors constantly
(third-party scripts, ResizeObserver loops, blocked trackers); gating a QA
verdict on them would make every run needs_review.

CONTRACT
--------
Best-effort, exactly like ai_run_capture.py and ai_eval.py: every public
method catches everything and returns a benign value. This runs inside the
live browser automation loop, so a bug in here must never abort, slow, or
alter a test run. If detection breaks, runs simply behave as they did before
this module existed.

Kill switches (all env-tunable, matching this codebase's convention):
    AI_ERROR_DETECTION_ENABLED=0            disable the whole feature
    AI_ERROR_DETECTION_VISION=0             disable tier 2 only (keep DOM scan)
    AI_ERROR_DETECTION_VISION_MAX_CALLS=N   tier-2 budget per run (default 10)
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Truncation budgets. _MAX_MESSAGE_CHARS is deliberately small: the message
# is appended onto the step description, and ai_eval._MAX_STEP_CHARS truncates
# each description to 300 chars before it reaches the GEval judge -- a verbose
# error would push the step's own text out of the judge's view.
_MAX_MESSAGE_CHARS = 160
_MAX_SIGNALS_PER_RUN = 25
_MAX_CONSOLE_ERRORS = 50
_MAX_FAILED_RESPONSES = 200

_DEFAULT_VISION_MAX_CALLS = 10

# Resource types whose 4xx/5xx responses are worth escalating on. Excludes
# image/stylesheet/font/media/script: a missing asset is a cosmetic defect,
# not "the feature under test failed", and including them made almost every
# real-world page look broken.
_ESCALATABLE_RESOURCE_TYPES = frozenset({"xhr", "fetch", "document"})

# Console noise that is never worth recording even as supporting evidence.
_CONSOLE_NOISE = re.compile(
    r"(favicon|ResizeObserver loop|Non-Error promise rejection|"
    r"chrome-extension://|net::ERR_BLOCKED_BY_CLIENT|Download the React DevTools)",
    re.IGNORECASE,
)


def _env_flag(name: str, default: bool) -> bool:
    """Read a 0/1/true/false env flag, falling back to `default` on anything
    unset or unparseable -- a bad deployment value must degrade to the
    documented default, never crash a run."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r is not a boolean — using default %s.", name, raw, default)
    return default


def _vision_budget() -> int:
    raw = os.environ.get("AI_ERROR_DETECTION_VISION_MAX_CALLS", "").strip()
    if not raw:
        return _DEFAULT_VISION_MAX_CALLS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "AI_ERROR_DETECTION_VISION_MAX_CALLS=%r is not an integer — using default %d.",
            raw, _DEFAULT_VISION_MAX_CALLS,
        )
        return _DEFAULT_VISION_MAX_CALLS
    if value < 0:
        logger.warning(
            "AI_ERROR_DETECTION_VISION_MAX_CALLS=%d is negative — using default %d.",
            value, _DEFAULT_VISION_MAX_CALLS,
        )
        return _DEFAULT_VISION_MAX_CALLS
    return value


def detection_enabled() -> bool:
    return _env_flag("AI_ERROR_DETECTION_ENABLED", True)


# ── Tier 1: DOM scan ────────────────────────────────────────────────────────
#
# Runs entirely inside the page (one round trip) and returns only the small
# set of elements that satisfy BOTH the semantics and the text test. Written
# as a plain JS expression string rather than an imported asset so the whole
# detection rule is readable in one place next to the reasoning above.
_DOM_SCAN_JS = r"""
() => {
  // Conservative "the app actually failed" vocabulary. See this module's
  // docstring for why validation words (invalid/required/denied) are absent.
  //
  // Deliberately NOT included, each removed for a specific false positive:
  //   cannot / can't  -> confirmation dialogs ("This action cannot be
  //                      undone") are routinely role="alertdialog", which
  //                      satisfies the semantics test, so this fired on an
  //                      ordinary Are-you-sure modal.
  //   timeout         -> settings labels ("Session timeout: 30 minutes").
  //                      "timed out" (the past tense, i.e. it happened) is
  //                      kept.
  //   try again       -> benign retry prompts ("Didn't get a code? Try
  //                      again"). Real errors that say "please try again"
  //                      almost always also say failed/error/went wrong, so
  //                      nothing is actually lost by dropping it.
  const HARD = /(\berror\b|\berrors\b|\bfailed\b|\bfailure\b|went wrong|unable to|could not|couldn't|timed out|unexpected)/i;

  // Veto list: text where an error word appears but is NOT reporting a
  // failure -- a count, a label, or an explicit negation. Checked after
  // HARD, so "no errors found" in a status panel can never be a finding.
  const BENIGN = /(no errors?\b|zero errors?\b|\b0 errors?\b|without errors?|error[-\s]?free|error log|error rate|error count|no failures?\b)/i;

  const SEM_SELECTORS = [
    '[role="alert"]',
    '[role="alertdialog"]',
    '[aria-live="assertive"]',
    '[class*="error" i]',
    '[class*="danger" i]',
    '[class*="toast" i]',
    '[class*="snackbar" i]',
    '[class*="notification" i]',
    '[class*="alert" i]',
    '[data-testid*="error" i]',
    '[data-test*="error" i]',
    '[id*="error" i]'
  ].join(',');

  const isVisible = (el) => {
    try {
      if (el.closest('[aria-hidden="true"]')) return false;
      const st = window.getComputedStyle(el);
      if (!st) return false;
      if (st.display === 'none' || st.visibility === 'hidden') return false;
      if (parseFloat(st.opacity || '1') === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  };

  // innerText is what we want (it reflects rendered text), but fall back to
  // textContent so the scan still works anywhere innerText is unavailable or
  // undefined rather than silently returning nothing at all.
  const textOf = (el) => {
    let t = '';
    try { t = el.innerText; } catch (e) { t = ''; }
    if (!t) { try { t = el.textContent; } catch (e) { t = ''; } }
    return (t || '').trim();
  };

  let nodes;
  try { nodes = Array.from(document.querySelectorAll(SEM_SELECTORS)); }
  catch (e) { return []; }

  // Cheap guard: a page with a huge number of matches is almost certainly
  // matching on generic class names, not real errors. Bail rather than
  // spend time on it or risk a flood of false positives.
  if (nodes.length > 400) return [];

  const candidates = nodes.filter((el) => {
    if (!isVisible(el)) return false;
    const text = textOf(el);
    // Empty = a wrapper; very long = a whole page region that merely
    // contains the word somewhere, not an error message itself.
    if (!text || text.length > 400) return false;
    if (BENIGN.test(text)) return false;
    return HARD.test(text);
  });

  // Keep only the innermost match of any nested chain, so one toast reported
  // through its wrapper + inner span doesn't become two separate signals.
  const innermost = candidates.filter(
    (el) => !candidates.some((other) => other !== el && el.contains(other))
  );

  return innermost.slice(0, 5).map((el) => ({
    text: textOf(el).replace(/\s+/g, ' ').slice(0, 400),
    role: el.getAttribute('role') || '',
    cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
    tag: el.tagName.toLowerCase()
  }));
}
"""

# ── Tier 2: vision escalation prompt ────────────────────────────────────────
#
# Same llm_router.complete(images_b64=..., expect_json=True) pattern already
# used by app/services/visual_judge.py and ai_eval.evaluate_expected_results
# rather than inventing a second vision-call convention.
_VISION_SYSTEM = (
    "You are a QA engineer looking at a screenshot of a web application "
    "taken immediately after a test automation step ran. Decide ONE thing: "
    "is the application currently showing the user an error, failure, or "
    "broken state (an error banner/toast/dialog, an error page, a failed "
    "operation message, a crashed or blank-where-content-should-be view)? "
    "Ordinary form-validation hints on fields the user has not filled in yet "
    "do NOT count. Loading spinners do NOT count. If you are not sure, "
    "answer false — a wrong 'true' wastes a QA engineer's time. Respond "
    'with JSON only: {"error_shown": bool, "message": str} where message is '
    "the error text visible on screen verbatim when error_shown is true, or "
    "an empty string otherwise."
)


def _normalize(text: str) -> str:
    """Dedup key: case- and whitespace-insensitive, length-bounded. Two
    renders of the same toast must collapse to one signal even if the DOM
    re-flowed between them."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()[:200]


class RunErrorWatcher:
    """Watches one browser context for application-level error states.

    Lifecycle, as used by ai_runner._execute_steps:
        watcher = RunErrorWatcher(context, page, run_id=run_id)
        await watcher.attach()          # wire console/network listeners
        await watcher.seed_baseline()   # after the initial navigation
        ...
        await watcher.check()           # after each agent action
        ...
        await watcher.detach()          # before browser.close()

    seed_baseline() matters: an error banner already on screen when the run
    starts (a stale session warning, a pre-existing failed widget) is NOT
    attributable to any action the agent took, and flagging it would make
    every run on that page needs_review. Baseline messages are pre-seeded
    into the dedup set so only genuinely NEW errors are ever reported.

    Each distinct error message is reported exactly once per run. A single
    broken feature that re-renders its toast on every retry is one finding,
    not twenty.
    """

    def __init__(self, context, page, run_id: Optional[str] = None):
        self._context = context
        self._page = page
        self._run_id = run_id
        self._seen: set[str] = set()
        self._signals: list[dict] = []
        self._console_errors: list[str] = []
        self._failed_responses: list[dict] = []
        self._vision_enabled = _env_flag("AI_ERROR_DETECTION_VISION", True)
        self._vision_calls_left = _vision_budget()
        # Strong references to the page objects already wired. Deliberately
        # NOT a set of id()s: ids are recycled after garbage collection, so a
        # new tab could inherit a dead page's id and silently never get
        # listeners attached.
        self._wired_pages: list = []
        self._attached = False

    # ── Properties ──────────────────────────────────────────────────────
    @property
    def signals(self) -> list[dict]:
        """Every confirmed application error found in this run, in order.
        Each: {message, source, detail, screenshot_url}."""
        return list(self._signals)

    # ── Listener wiring ─────────────────────────────────────────────────
    def _wire_page(self, page) -> None:
        """Attach console/response listeners to one page. Idempotent per
        page object -- browser-use can hand the same page back repeatedly."""
        if any(p is page for p in self._wired_pages):
            return
        self._wired_pages.append(page)

        def _on_console(msg) -> None:
            try:
                # Playwright exposes these as properties, but has shipped them
                # as methods in older releases — resolve both so a version
                # bump can't silently turn every console message into noise.
                msg_type = msg.type
                if callable(msg_type):
                    msg_type = msg_type()
                if msg_type != "error":
                    return
                text = msg.text
                if callable(text):
                    text = text()
                text = (text or "").strip()
                if not text or _CONSOLE_NOISE.search(text):
                    return
                if len(self._console_errors) < _MAX_CONSOLE_ERRORS:
                    self._console_errors.append(text[:300])
            except Exception:
                # A listener must never raise into Playwright's dispatch loop.
                pass

        def _on_response(response) -> None:
            try:
                status = response.status
                if status < 400:
                    return
                try:
                    resource_type = response.request.resource_type
                except Exception:
                    resource_type = ""
                if resource_type not in _ESCALATABLE_RESOURCE_TYPES:
                    return
                # Bounded: check() normally drains this every action, but it
                # returns early once the per-run signal cap is hit, and a
                # chatty app must not be able to grow this without limit for
                # the rest of a long run.
                if len(self._failed_responses) >= _MAX_FAILED_RESPONSES:
                    return
                self._failed_responses.append(
                    {"status": status, "url": (response.url or "")[:300]}
                )
            except Exception:
                pass

        try:
            page.on("console", _on_console)
            page.on("response", _on_response)
        except Exception:
            logger.debug(
                "Error detection: could not wire listeners on a page (run_id=%s)",
                self._run_id, exc_info=True,
            )

    async def attach(self) -> None:
        """Wire listeners on every existing page and on any page opened
        later (the agent can open a tab mid-run). Never raises."""
        try:
            for page in list(getattr(self._context, "pages", []) or []):
                self._wire_page(page)

            def _on_new_page(page) -> None:
                try:
                    self._wire_page(page)
                except Exception:
                    pass

            self._context.on("page", _on_new_page)
            self._attached = True
        except Exception:
            logger.warning(
                "Error detection: attach failed (run_id=%s) — continuing "
                "without in-run error detection for this run.",
                self._run_id, exc_info=True,
            )

    async def detach(self) -> None:
        """Best-effort teardown. Playwright drops listeners with the context
        anyway; this just stops any late event from mutating our state."""
        self._attached = False

    # ── Page resolution ─────────────────────────────────────────────────
    def _active_page(self):
        """The page the agent is most likely driving right now.

        The `page` captured at run start goes stale the moment the agent
        opens a new tab, and browser-use switches to the newest one. Prefer
        the last open page in the context, falling back to the original.
        """
        try:
            pages = [p for p in (getattr(self._context, "pages", []) or []) if not p.is_closed()]
            if pages:
                return pages[-1]
        except Exception:
            pass
        return self._page

    # ── Tier 1 ──────────────────────────────────────────────────────────
    async def _dom_scan(self, page) -> list[dict]:
        try:
            result = await page.evaluate(_DOM_SCAN_JS)
        except Exception:
            # Extremely common and completely benign: the page navigated or
            # the execution context was destroyed mid-evaluate. Debug level
            # on purpose so it can't spam worker logs on a busy SPA.
            logger.debug(
                "Error detection: DOM scan failed (run_id=%s)", self._run_id, exc_info=True
            )
            return []
        return result if isinstance(result, list) else []

    # ── Tier 2 ──────────────────────────────────────────────────────────
    async def _vision_scan(self, page, failed: list[dict]) -> Optional[dict]:
        """Ask the vision model whether the user is actually being shown an
        error. Only called when tier 1 found nothing AND a real API call
        failed. Returns {message, detail, screenshot_url} or None."""
        if not self._vision_enabled or self._vision_calls_left <= 0:
            return None

        try:
            shot = await page.screenshot()
        except Exception:
            logger.debug(
                "Error detection: screenshot for vision escalation failed (run_id=%s)",
                self._run_id, exc_info=True,
            )
            return None

        screenshot_b64 = base64.b64encode(shot).decode()
        self._vision_calls_left -= 1

        failed_desc = "; ".join(f"{f['status']} {f['url']}" for f in failed[:5])
        prompt = (
            "While the last test step ran, these API requests failed:\n"
            f"{failed_desc}\n\n"
            "Based only on the attached screenshot of the application after "
            "that step, is the user being shown an error or broken state?"
        )

        try:
            # llm_router.complete is a synchronous/blocking call (litellm).
            # It MUST NOT run on this event loop -- the loop is driving the
            # live browser session, and blocking it would stall the agent
            # and the CDP screencast for the duration of an LLM round trip.
            from app.services import llm_router

            result = await asyncio.to_thread(
                llm_router.complete,
                prompt,
                system=_VISION_SYSTEM,
                images_b64=[screenshot_b64],
                expect_json=True,
                max_tokens=512,
            )
        except Exception as exc:
            logger.warning(
                "Error detection: vision escalation failed for run_id=%s "
                "(run continues normally): %s",
                self._run_id, exc,
            )
            return None

        raw = result.parsed_json or {}
        if not raw.get("error_shown"):
            return None
        message = str(raw.get("message") or "").strip() or "Application error visible on screen"
        return {
            "message": message,
            "detail": f"failed requests: {failed_desc}",
            "screenshot_url": "data:image/png;base64," + screenshot_b64,
        }

    # ── Public check ────────────────────────────────────────────────────
    async def check(self) -> Optional[dict]:
        """Inspect the live page after an action. Returns a signal dict
        {message, source, detail, screenshot_url} the first time a given
        application error is seen, or None. NEVER raises -- a failure here
        returns None and the run proceeds exactly as it would have before
        this module existed.
        """
        if not self._attached or not detection_enabled():
            return None
        if len(self._signals) >= _MAX_SIGNALS_PER_RUN:
            return None

        try:
            page = self._active_page()
            if page is None:
                return None

            # Drain the network buffer for THIS window regardless of which
            # tier fires, so a later check can't escalate on a stale failure.
            failed = self._failed_responses
            self._failed_responses = []

            signal: Optional[dict] = None

            for hit in await self._dom_scan(page):
                key = _normalize(hit.get("text", ""))
                if not key or key in self._seen:
                    continue
                self._seen.add(key)
                signal = {
                    "message": hit.get("text", "").strip()[:_MAX_MESSAGE_CHARS],
                    "source": "dom",
                    "detail": (
                        f"<{hit.get('tag', '?')} role={hit.get('role') or '-'} "
                        f"class={hit.get('cls') or '-'}>"
                    ),
                    "screenshot_url": None,
                }
                break

            if signal is None and failed:
                vision = await self._vision_scan(page, failed)
                if vision is not None:
                    key = _normalize(vision["message"])
                    if key and key not in self._seen:
                        self._seen.add(key)
                        signal = {
                            "message": vision["message"][:_MAX_MESSAGE_CHARS],
                            "source": "vision",
                            "detail": vision["detail"],
                            "screenshot_url": vision["screenshot_url"],
                        }

            if signal is None:
                return None

            # DOM-tier signals have no screenshot yet. Capture one now --
            # only on the rare confirmed-error path, so per-step screenshot
            # cost is not reintroduced for normal runs (and this is the only
            # visual evidence available at all on live-capture runs, which
            # deliberately skip per-step screenshots).
            if signal["screenshot_url"] is None:
                try:
                    shot = await page.screenshot()
                    signal["screenshot_url"] = (
                        "data:image/png;base64," + base64.b64encode(shot).decode()
                    )
                except Exception:
                    logger.debug(
                        "Error detection: evidence screenshot failed (run_id=%s)",
                        self._run_id, exc_info=True,
                    )

            if self._console_errors:
                # Supporting evidence only -- never the trigger. See docstring.
                signal["detail"] = (
                    f"{signal['detail']} | console: {self._console_errors[-1][:160]}"
                )

            self._signals.append(signal)
            logger.warning(
                "Error detection (run_id=%s): application error detected via "
                "%s after an agent action — %r. Step will be marked failed "
                "and the run flagged for review.",
                self._run_id, signal["source"], signal["message"],
            )
            return signal
        except Exception:
            logger.warning(
                "Error detection: check() failed unexpectedly (run_id=%s) — "
                "treating as 'no error found' so the run is unaffected.",
                self._run_id, exc_info=True,
            )
            return None

    async def seed_baseline(self) -> None:
        """Record whatever error-looking content is ALREADY on screen before
        the agent takes its first action, so it is never attributed to an
        agent step. Also clears the network/console buffers filled during
        initial page load. Never raises."""
        if not self._attached or not detection_enabled():
            return
        try:
            page = self._active_page()
            if page is None:
                return
            for hit in await self._dom_scan(page):
                key = _normalize(hit.get("text", ""))
                if key:
                    self._seen.add(key)
                    logger.info(
                        "Error detection (run_id=%s): pre-existing page message "
                        "baselined and will not be reported as a run failure: %r",
                        self._run_id, hit.get("text", "")[:120],
                    )
        except Exception:
            logger.debug(
                "Error detection: baseline seed failed (run_id=%s)",
                self._run_id, exc_info=True,
            )
        finally:
            self._failed_responses = []
            self._console_errors = []
