"""
ai_locator_suggester.py
────────────────────────────────────────────────────────────────────────────────
Robot Framework library — Gemini AI locator suggestion on test failure.

Flow:
  1. Called from evidence_keywords.resource teardown when TEST STATUS == FAIL.
  2. Reads captured HTML from test-artifacts/html/{suite_name}/{test_name}.html.
  3. Extracts failed locator from RF failure message (regex).
  4. Sends HTML + failed locator to Gemini API with structured prompt.
  5. Saves JSON suggestion file to:
       test-artifacts/ai_suggestions/{suite_name}/{test_name}.json
  6. Human reviews the file and sets "approved": true/false.
  7. python libs/hitl_manager.py apply  →  patches page object + locators.resource.

Folder structure:
  test-artifacts/ai_suggestions/{suite_name}/{test_case_name}.json
  (one folder per test script, one file per test case)

Key rotation / retry policy:
  - Keys: GEMINI_API_KEYS (comma-separated) or GEMINI_API_KEY_1, _2, _3 ...
  - On HTTP 429 (rate limit): rotate to next key immediately, wait ROTATE_WAIT_S.
  - On timeout / server error: retry same key with exponential backoff.
  - All keys exhausted: wait ALL_KEYS_WAIT_S, then retry from key 0.
  - Max total attempts: MAX_ATTEMPTS (default 6).
  - All errors are logged and swallowed — never fail the test from this library.

Gemini model: gemini-2.5-flash-lite (free tier, 15 RPM / 1500 RPD per key).
NOTE: gemini-2.0-flash was shut down June 1 2026. Use 2.5-flash-lite or 2.5-flash.

AI runs in a background daemon thread — teardown returns immediately.
Suggestions are written asynchronously to test-artifacts/ai_suggestions/.
────────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Absolute path so the script works regardless of working directory.
# libs/ai_locator_suggester.py lives one level below the project root,
# so parents[1] is the project root and test-artifacts/ is always found.
ARTIFACTS_ROOT = Path(__file__).resolve().parents[1] / "test-artifacts"


def _load_dotenv() -> None:
    """Load repo-local .env values when the shell has not exported them.

    Allows ``python libs/ai_locator_suggester.py`` to find GEMINI_API_KEY_*
    without requiring the caller to ``export`` variables first.  Only sets a
    variable if it is NOT already in os.environ so shell-level overrides win.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key   = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env before reading any constants from os.environ — covers both CLI runs
# (where nothing has loaded .env yet) and the edge case where this module is
# imported before resources/variables/config.py.
_load_dotenv()

# ── Retry / rotation config (overridable via env vars) ────────────────────────
MAX_ATTEMPTS     = int(os.environ.get("AI_MAX_ATTEMPTS",    "3"))
ROTATE_WAIT_S    = float(os.environ.get("AI_ROTATE_WAIT",   "2"))    # wait after key rotate
BACKOFF_BASE_S   = float(os.environ.get("AI_BACKOFF_BASE",  "3"))    # first retry wait
ALL_KEYS_WAIT_S  = float(os.environ.get("AI_ALL_KEYS_WAIT", "60"))   # all keys exhausted
GEMINI_MODEL     = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_TIMEOUT_S = int(os.environ.get("GEMINI_TIMEOUT",    "60"))
# google-genai HttpOptions.timeout is in MILLISECONDS — convert from seconds.
GEMINI_TIMEOUT_MS = GEMINI_TIMEOUT_S * 1000
HTML_MAX_CHARS   = int(os.environ.get("AI_HTML_MAX_CHARS", "12000"))  # ~12 KB — free tier sweet spot
# Expected output is a small structured JSON object — cap generation to bound
# worst-case latency and prevent runaway completions from contributing to timeouts.
MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "2048"))


def _sanitize(name: str) -> str:
    """Replace spaces and filesystem-unsafe characters with underscores."""
    return re.sub(r'[<>:"/\\|?*\n\r\t ]', "_", name).strip("_") or "unnamed"


def _load_api_keys() -> list[str]:
    """
    Load Gemini API keys from environment.
    Supports two formats:
      GEMINI_API_KEYS=key1,key2,key3          ← comma-separated
      GEMINI_API_KEY_1=key1 + GEMINI_API_KEY_2=key2 + ...
    Both formats may be combined; duplicates are removed.
    """
    keys: list[str] = []

    # Format 1: comma-separated list
    raw = os.environ.get("GEMINI_API_KEYS", "").strip()
    if raw:
        for k in raw.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)

    # Format 2: individual numbered vars
    i = 1
    while True:
        k = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
        if not k:
            break
        if k not in keys:
            keys.append(k)
        i += 1

    return keys


def _extract_locator_from_message(message: str) -> str:
    """
    Try to extract the failed locator string from a Robot Framework failure message.
    RF messages look like:
      - waiting for locator('xpath=//div[...]')
      - strict mode violation: locator('css=.foo') resolved to 2 elements
      - Wait For Elements State('xpath=...', visible, 15s) failed.

    Returns the extracted locator or EMPTY string if not found.

    NOTE: Playwright wraps the locator as locator('...') and backslash-escapes any
    inner quotes (e.g. \\'). A naive non-greedy match (.+?) stops at the FIRST inner
    quote and truncates the locator — that is the bug that produced
    "//div[contains(@class,\\'rounded-3xl\\" in the suggestion files. The patterns
    below consume an escaped char as a unit ((?:\\.|[^'])*) so the whole locator is
    captured, then we unescape the quotes so the stored locator is clean.
    """
    patterns = [
        r"locator\('((?:\\.|[^'])*)'\)",      # locator('...')  single-quoted, escape-aware
        r'locator\("((?:\\.|[^"])*)"\)',      # locator("...")  double-quoted, escape-aware
        r"Wait For Elements State\(['\"](.+?)['\"]",   # RF keyword call
        r"(?:xpath|css|id|data-testid)=\S+",  # bare locator string
    ]
    for pat in patterns:
        m = re.search(pat, message, re.IGNORECASE | re.DOTALL)
        if m:
            result = m.group(1) if m.lastindex else m.group(0)
            # Unescape the backslash-escaped quotes Playwright added to the message.
            result = result.replace("\\'", "'").replace('\\"', '"')
            return result.strip()[:500]   # cap at 500 chars
    return ""


def _preprocess_html(html: str, max_chars: int) -> str:
    """
    Strip JS, CSS, and SVG blocks from raw page HTML before sending to Gemini.
    These add thousands of characters that provide zero locator signal.
    Collapsing whitespace then truncating gives Gemini a clean DOM skeleton.
    """
    # Remove entire <script>…</script>, <style>…</style>, <svg>…</svg> blocks
    for tag in ("script", "style", "svg", "noscript", "link", "meta"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # Remove self-closing tags that add no structural info
    html = re.sub(r"<(link|meta|input)[^>]*/?>", "", html, flags=re.IGNORECASE)
    # Collapse all whitespace runs to a single space
    html = re.sub(r"\s+", " ", html).strip()
    return html[:max_chars]


def _build_prompt(failed_locator: str, failure_message: str, html_snippet: str) -> str:
    """Build the structured Gemini prompt."""
    locator_block = (
        f"FAILED LOCATOR: {failed_locator}"
        if failed_locator
        else "FAILED LOCATOR: (could not extract — see failure message)"
    )
    return f"""You are a QA engineer specialising in web test automation with Playwright and Robot Framework.

TASK: A test failed because a locator could not be found or matched more than one element.
Analyse the HTML below and suggest 2–3 alternative locators that uniquely identify the intended element.

{locator_block}

FAILURE MESSAGE:
{failure_message[:800]}

PAGE HTML (may be truncated to {HTML_MAX_CHARS} chars):
{html_snippet}

STRICT RULES — every suggestion must follow these:
1. NEVER use absolute XPath starting with /html/body/
2. NEVER use index-based XPath like //div[3] or //ul/li[2]
3. ALWAYS normalize text: //div[normalize-space()='Label'] not //div[text()='Label']
4. Locator priority: data-testid > id attribute > CSS selector > relative XPath
5. Each locator MUST resolve to EXACTLY 1 element in this DOM
6. For Tailwind mobile-first UIs rendered twice: exclude mobile copy with
   not(ancestor::div[contains(@class,'lg:hidden')])
7. Prefix every locator: xpath=... or css=... (Browser library format)
8. For sibling values (metric cards), navigate from label to value:
   //div[normalize-space()='Label']/parent::div/following-sibling::div/div[...]

RESPOND WITH VALID JSON ONLY — no markdown fences, no explanation outside JSON:
{{
  "analysis": "One sentence: why the original locator likely broke.",
  "suggestions": [
    {{
      "locator": "xpath=... or css=...",
      "confidence": "high | medium | low",
      "explanation": "Why this locator is stable and unique.",
      "approved": null
    }}
  ]
}}"""


class AILocatorSuggester:

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._key_index: int = 0
        self._keys_loaded: bool = False

    # ── Key management ─────────────────────────────────────────────────────────

    def _ensure_keys(self) -> None:
        if not self._keys_loaded:
            self._keys = _load_api_keys()
            self._keys_loaded = True
            if self._keys:
                logger.info(
                    f"[AILocatorSuggester] Loaded {len(self._keys)} Gemini API key(s)."
                )
            else:
                logger.warning(
                    "[AILocatorSuggester] No Gemini API keys found. "
                    "Set GEMINI_API_KEYS or GEMINI_API_KEY_1 in .env"
                )

    def _current_key(self) -> Optional[str]:
        self._ensure_keys()
        if not self._keys:
            return None
        return self._keys[self._key_index % len(self._keys)]

    def _rotate_key(self) -> bool:
        """
        Rotate to the next API key.
        Returns False if we've wrapped around (all keys tried).
        """
        if not self._keys:
            return False
        old_index = self._key_index
        self._key_index = (self._key_index + 1) % len(self._keys)
        wrapped = self._key_index <= old_index and len(self._keys) > 1
        logger.info(
            f"[AILocatorSuggester] Rotated to key index {self._key_index} "
            f"(wrapped={wrapped})"
        )
        return wrapped  # True means all keys exhausted this round

    # ── Gemini call with retry / rotation ─────────────────────────────────────

    def _call_gemini(self, prompt: str) -> tuple[Optional[str], str]:
        """
        Call Gemini API with key rotation and exponential backoff.
        Uses google-genai (unified SDK for Gemini 2.0+).

        Returns (response_text, error_reason).
          - On success: (text, "")
          - On failure: (None, human-readable reason)

        Client construction is defensive — tries with http_options first, then
        without, so an SDK version mismatch on that parameter never kills the call.
        """
        try:
            from google import genai  # google-genai unified SDK
        except ImportError:
            msg = "google-genai not installed. Run: pip install google-genai"
            logger.error(f"[AILocatorSuggester] {msg}")
            return None, msg

        attempt = 0
        backoff = BACKOFF_BASE_S
        keys_exhausted_count = 0
        last_error = "unknown"

        while attempt < MAX_ATTEMPTS:
            key = self._current_key()
            if not key:
                msg = "No API keys configured — set GEMINI_API_KEYS or GEMINI_API_KEY_1 in .env"
                logger.warning(f"[AILocatorSuggester] {msg}")
                return None, msg

            attempt += 1
            logger.info(
                f"[AILocatorSuggester] Gemini call attempt {attempt}/{MAX_ATTEMPTS} "
                f"(key index {self._key_index}, model={GEMINI_MODEL})"
            )

            try:
                # Defensive client construction:
                # Some SDK versions reject http_options as a plain dict — fall back
                # to a bare client if that happens.
                try:
                    client = genai.Client(
                        api_key=key,
                        # NOTE: timeout is in MILLISECONDS in google-genai.
                        http_options={"timeout": GEMINI_TIMEOUT_MS},
                    )
                except (TypeError, ValueError) as client_exc:
                    logger.warning(
                        f"[AILocatorSuggester] http_options rejected "
                        f"({type(client_exc).__name__}) — using bare client: {client_exc}"
                    )
                    client = genai.Client(api_key=key)

                # ── Streaming call (preferred) ─────────────────────────────────
                # Streaming keeps the TCP connection alive while tokens arrive,
                # so ReadTimeout cannot fire on slow free-tier responses.
                # Falls back to non-streaming if the SDK version lacks the method.
                # Bound the generation: small structured object, JSON-only output.
                gen_config = {
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                }
                text = ""
                try:
                    stream = client.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=gen_config,
                    )
                    for chunk in stream:
                        if hasattr(chunk, "text") and chunk.text:
                            text += chunk.text
                    text = text.strip()
                except AttributeError:
                    # SDK too old — generate_content_stream not available
                    logger.warning(
                        "[AILocatorSuggester] Streaming not available — "
                        "falling back to non-streaming."
                    )
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=gen_config,
                    )
                    text = response.text.strip() if response.text else ""

                if text:
                    logger.info("[AILocatorSuggester] Gemini responded successfully.")
                    return text, ""
                logger.warning("[AILocatorSuggester] Gemini returned empty response.")
                return None, "Empty response from Gemini API"

            except Exception as exc:
                exc_type = type(exc).__name__
                exc_str  = str(exc).lower()
                last_error = f"{exc_type}: {exc}"

                logger.warning(
                    f"[AILocatorSuggester] Attempt {attempt} failed "
                    f"[{exc_type}]: {exc}"
                )

                # ── Rate limit (429 / RESOURCE_EXHAUSTED) → rotate key ────────
                if (
                    "429"              in exc_str
                    or "quota"         in exc_str
                    or "rate limit"    in exc_str
                    or "resource_exhausted" in exc_str
                    or "resourceexhausted"  in exc_str
                ):
                    wrapped = self._rotate_key()
                    if wrapped:
                        keys_exhausted_count += 1
                        logger.warning(
                            f"[AILocatorSuggester] All keys exhausted "
                            f"({keys_exhausted_count}x). Waiting {ALL_KEYS_WAIT_S}s..."
                        )
                        time.sleep(ALL_KEYS_WAIT_S)
                        backoff = BACKOFF_BASE_S
                    else:
                        time.sleep(ROTATE_WAIT_S)
                    continue

                # ── Model not found / invalid key / bad request → fatal ────────
                # These will never succeed no matter how many retries.
                elif (
                    "404"               in exc_str
                    or "not found"      in exc_str
                    or "invalid_argument" in exc_str
                    or "invalid argument" in exc_str
                    or "permission_denied" in exc_str
                    or "api_key"        in exc_str
                    or "api key"        in exc_str
                    or "unauthorized"   in exc_str
                    or "403"            in exc_str
                ):
                    logger.error(
                        f"[AILocatorSuggester] Fatal API error [{exc_type}] — "
                        f"check model name ('{GEMINI_MODEL}') and API key: {exc}"
                    )
                    return None, last_error

                # ── Timeout / SSL / network → backoff, retry same key ─────────
                elif (
                    "timeout"     in exc_str
                    or "timed out"  in exc_str
                    or "deadline"   in exc_str
                    or "ssl"        in exc_str
                    or "handshake"  in exc_str
                    or "connection" in exc_str
                    or "network"    in exc_str
                    or "503"        in exc_str
                    or "500"        in exc_str
                    or "unavailable" in exc_str
                ):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

                # ── Unknown error → abort immediately ─────────────────────────
                else:
                    logger.error(
                        f"[AILocatorSuggester] Non-retryable [{exc_type}]: {exc}"
                    )
                    return None, last_error

        msg = f"All {MAX_ATTEMPTS} attempts failed. Last error: {last_error}"
        logger.error(f"[AILocatorSuggester] {msg}")
        return None, msg

    # ── Response parsing ───────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict:
        """
        Parse Gemini JSON response. Strips markdown fences if present.
        Returns a dict with 'analysis' and 'suggestions' keys.
        Falls back to an error structure on parse failure.
        """
        # Strip ```json ... ``` or ``` ... ``` fences
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(clean)
            # Ensure required keys exist
            data.setdefault("analysis", "No analysis provided.")
            data.setdefault("suggestions", [])
            return data
        except json.JSONDecodeError as exc:
            logger.error(f"[AILocatorSuggester] JSON parse failed: {exc}. Raw: {raw[:300]}")
            return {
                "analysis": "AI response could not be parsed as JSON.",
                "suggestions": [],
                "raw_response": raw[:1000],
            }

    # ── Public RF keyword ──────────────────────────────────────────────────────

    def suggest_locators_for_failure(
        self,
        suite_name: str,
        test_name: str,
        failure_message: str,
    ) -> str:
        """
        RF keyword: Suggest Locators For Failure
        Called from evidence_keywords.resource teardown when TEST STATUS == FAIL.

        ENQUEUE ONLY — does NO network I/O during the test run.

        Why: previously this spawned an unbounded background daemon thread per
        failure that streamed from Gemini for up to 60s. During a failure cascade
        that produced dozens of concurrent long-lived threads, saturating the
        network/GIL and causing the next suite's `page.goto` to time out — turning
        one failure into a whole-suite meltdown. AI work MUST NOT run concurrently
        with live Playwright navigation.

        Instead we write a lightweight 'queued' record and return instantly. The
        actual Gemini analysis runs AFTER the run via:
            python libs/ai_locator_suggester.py            (process the queue)
        or by calling process_queue() from a post-run CI step.

        Navigation/setup failures have no locator to heal, so they are skipped
        (no point asking the AI to fix a `page.goto` timeout).

        Always returns empty string — the result is the JSON file, not the return value.
        """
        try:
            self._enqueue(suite_name, test_name, failure_message)
        except Exception as exc:  # never let evidence capture fail a test
            logger.error(f"[AILocatorSuggester] Enqueue failed for '{test_name}': {exc}")
        return ""

    def _enqueue(self, suite_name: str, test_name: str, failure_message: str) -> None:
        """Write a cheap 'queued' record (no network) for later offline processing."""
        failed_locator = _extract_locator_from_message(failure_message)
        if not failed_locator:
            logger.info(
                f"[AILocatorSuggester] No locator in failure message for "
                f"'{test_name}' (likely a setup/navigation failure) — not queued."
            )
            return
        self._save_suggestions(
            suite_name=suite_name,
            test_name=test_name,
            failed_locator=failed_locator,
            failure_message=failure_message,
            parsed={"analysis": "", "suggestions": []},
            status="queued",
            error_reason="",
            full_failure_message=failure_message,
        )
        logger.info(f"[AILocatorSuggester] Queued AI analysis for: {test_name}")

    # ── Offline queue processing (run AFTER the suite, never during) ───────────

    def process_queue(self) -> int:
        """
        Process every 'queued' suggestion file: call Gemini sequentially (one at a
        time — bounded concurrency) and rewrite each file with the result.

        Returns the number of records processed. Safe to run repeatedly.
        """
        queue_root = ARTIFACTS_ROOT / "ai_suggestions"
        if not queue_root.exists():
            logger.info("[AILocatorSuggester] No ai_suggestions folder — nothing to process.")
            return 0

        processed = 0
        for json_path in sorted(queue_root.rglob("*.json")):
            try:
                record = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"[AILocatorSuggester] Skipping unreadable {json_path}: {exc}")
                continue
            if record.get("status") != "queued":
                continue
            suite_name = record.get("suite_name", "")
            test_name = record.get("test_name", "")
            failure_message = record.get(
                "failure_message", record.get("failure_message_excerpt", "")
            )
            logger.info(f"[AILocatorSuggester] Processing queued: {suite_name} / {test_name}")
            self._suggest_safe(suite_name, test_name, failure_message)
            processed += 1

        logger.info(f"[AILocatorSuggester] Queue processing complete — {processed} record(s).")
        return processed

    def _suggest_safe(self, suite_name: str, test_name: str, failure_message: str) -> None:
        """Background wrapper — catches all exceptions so daemon thread never crashes silently."""
        try:
            self._suggest(suite_name, test_name, failure_message)
        except Exception as exc:
            logger.error(
                f"[AILocatorSuggester] Background thread error for '{test_name}': {exc}"
            )

    def _suggest(self, suite_name: str, test_name: str, failure_message: str) -> str:
        safe_suite = _sanitize(suite_name)
        safe_test  = _sanitize(test_name)

        # ── Read captured HTML ─────────────────────────────────────────────────
        html_path = ARTIFACTS_ROOT / "html" / safe_suite / f"{safe_test}.html"
        logger.info(f"[AILocatorSuggester] Looking for HTML at: {html_path}")
        if html_path.exists():
            raw_html = html_path.read_text(encoding="utf-8")
            html = _preprocess_html(raw_html, HTML_MAX_CHARS)
            logger.info(
                f"[AILocatorSuggester] HTML preprocessed: "
                f"{len(raw_html)} → {len(html)} chars."
            )
        else:
            logger.warning(
                f"[AILocatorSuggester] No HTML snapshot found at {html_path}. "
                f"Tip: check that suite_name in the JSON matches the folder under "
                f"test-artifacts/html/ (suite name comes from ${{SUITE NAME}} in RF)."
                f" Proceeding without page context."
            )
            html = "(HTML snapshot not available)"

        # ── Extract failed locator from RF failure message ─────────────────────
        failed_locator = _extract_locator_from_message(failure_message)
        if failed_locator:
            logger.info(f"[AILocatorSuggester] Extracted locator: {failed_locator}")
        else:
            logger.warning(
                "[AILocatorSuggester] Could not extract locator from failure message. "
                "Gemini will analyse from context only."
            )

        # ── Check if API keys are available before building (expensive) prompt ─
        self._ensure_keys()
        if not self._keys:
            logger.warning(
                "[AILocatorSuggester] Skipping Gemini call — no API keys configured."
            )
            return self._save_suggestions(
                suite_name=suite_name,
                test_name=test_name,
                failed_locator=failed_locator,
                failure_message=failure_message,
                parsed={
                    "analysis": "Skipped — no GEMINI_API_KEYS configured.",
                    "suggestions": [],
                },
                status="skipped",
                error_reason="No API keys configured",
            )

        # ── Build prompt and call Gemini ───────────────────────────────────────
        prompt = _build_prompt(failed_locator, failure_message, html)
        raw_response, error_reason = self._call_gemini(prompt)

        if raw_response is None:
            parsed = {
                "analysis": f"AI call failed: {error_reason}",
                "suggestions": [],
            }
            status = "ai_error"
        else:
            parsed = self._parse_response(raw_response)
            status = "pending_review"
            error_reason = ""
            # Ensure approved field is null on all suggestions
            for s in parsed.get("suggestions", []):
                s.setdefault("approved", None)
                s.setdefault("applied", False)

        return self._save_suggestions(
            suite_name=suite_name,
            test_name=test_name,
            failed_locator=failed_locator,
            failure_message=failure_message,
            parsed=parsed,
            status=status,
            error_reason=error_reason,
        )

    def _save_suggestions(
        self,
        suite_name: str,
        test_name: str,
        failed_locator: str,
        failure_message: str,
        parsed: dict,
        status: str,
        error_reason: str = "",
        full_failure_message: str = "",
    ) -> str:
        """Write suggestions JSON to test-artifacts/ai_suggestions/{suite_name}/{test_name}.json."""
        out_dir = ARTIFACTS_ROOT / "ai_suggestions" / _sanitize(suite_name)
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "suite_name": suite_name,
            "test_name": test_name,
            "failed_locator": failed_locator,
            "failure_message_excerpt": failure_message[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": GEMINI_MODEL,
            "status": status,
            "analysis": parsed.get("analysis", ""),
            "suggestions": parsed.get("suggestions", []),
        }
        # Persist the full failure message on queued records so the offline pass
        # can re-extract the locator from the complete text, not a 500-char excerpt.
        if full_failure_message:
            payload["failure_message"] = full_failure_message
        # Surface the exact error so users don't have to dig through RF logs
        if error_reason:
            payload["error_reason"] = error_reason
        if "raw_response" in parsed:
            payload["raw_response"] = parsed["raw_response"]

        out_path = out_dir / f"{_sanitize(test_name)}.json"
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"[AILocatorSuggester] Suggestions saved → {out_path}")
        return str(out_path.resolve())


# ── Module-level RF keyword functions ─────────────────────────────────────────

_SUGGESTER = AILocatorSuggester()


def suggest_locators_for_failure(suite_name: str, test_name: str, failure_message: str) -> str:
    return _SUGGESTER.suggest_locators_for_failure(suite_name, test_name, failure_message)


def process_queue() -> int:
    """Module-level helper so CI / scripts can run the offline AI pass."""
    return _SUGGESTER.process_queue()


# ── CLI entrypoint — run the AI analysis AFTER the robot run ──────────────────
#   robot ...                              # run tests (enqueues only, no AI calls)
#   python libs/ai_locator_suggester.py    # process the queue offline
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    count = process_queue()
    print(f"[AILocatorSuggester] Processed {count} queued failure(s).")
