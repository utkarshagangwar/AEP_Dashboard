"""Android Vibe Testing "Hands" — a Brain/Hands loop that drives a
BrowserStack App Automate Appium session from a natural-language goal.

Mirrors app.services.ai_runner.run_ai_test_sync()'s signature, event shape,
and safety-gate philosophy so the rest of the pipeline (the Celery task,
the SSE stream, the Results/Skills tabs) needs no platform-specific handling
downstream — see app.workers.tasks.ai_execution's platform branch.

No local Appium server, no Android SDK, no emulator: Appium-Python-Client
talks directly to BrowserStack's hosted Appium hub over HTTPS (see
app.services.device_farm). Unlike ai_runner.py's Playwright/browser-use
path, this needs no asyncio event loop — Appium-Python-Client is a
synchronous, Selenium-derived client, so the whole loop is plain
synchronous Python with a time.monotonic() deadline standing in for
asyncio.wait_for.

Each step is reasoned from the UiAutomator accessibility-tree XML
(driver.page_source), not a screenshot sent to the model — resource-ids and
exact bounds are already in that text, so this is both cheaper per step and
never sends device pixels to the LLM (the same credential-leak discipline
ai_runner.py enforces via use_vision=False, achieved here by construction).
A screenshot is still captured every step, purely for the live SSE feed and
the tap-verification guardrail below.
"""
import base64
import io
import time
from typing import Callable, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_STUCK_REPEAT_THRESHOLD = 3
_TAP_DIFF_MIN_RATIO = 0.001  # near-zero pixel diff on a tap is suspicious
_MAX_PAGE_SOURCE_CHARS = 12000  # bounds prompt size/cost per step
_PLACEHOLDER_PREFIX = "<cred:"


def _truncate_page_source(xml: str) -> str:
    if len(xml) <= _MAX_PAGE_SOURCE_CHARS:
        return xml
    return xml[:_MAX_PAGE_SOURCE_CHARS] + "\n<!-- truncated -->"


def _build_prompt(goal: str, page_source: str, history: list[str]) -> tuple[str, str]:
    system = (
        "You are an Android UI test agent. You are given a goal, the current "
        "screen's UiAutomator accessibility-tree XML, and a history of actions "
        "already taken. Respond with STRICT JSON ONLY (no markdown fences, no "
        'prose) matching exactly this shape:\n'
        '{"action": "tap|swipe|input|assert|done|fail", '
        '"target": {"resource_id": "...", "accessibility_id": "..."}, '
        '"value": "...", "reasoning": "one short sentence", '
        '"assertion_expected": "..."}\n'
        "Prefer resource_id over accessibility_id when both are available. "
        'For "swipe", value is one of up/down/left/right. For "input", value '
        "is the text to type — if the goal implies a login credential, use the "
        "literal placeholder tokens <cred:username> or <cred:password> instead "
        'of ever guessing or inventing a real value. Use "done" once the goal '
        'is verifiably accomplished, "fail" if it clearly cannot be. Use '
        '"assert" to check for text/state without any device interaction.'
    )
    history_block = "\n".join(history[-15:]) or "(no actions yet)"
    prompt = (
        f"Goal: {goal}\n\n"
        f"Action history so far:\n{history_block}\n\n"
        f"Current screen (UiAutomator XML, possibly truncated):\n{page_source}"
    )
    return system, prompt


def _ask_brain(llm, goal: str, page_source: str, history: list[str]) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.services.llm_router import _extract_json

    system, prompt = _build_prompt(goal, page_source, history)
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
    text = response.content if isinstance(response.content, str) else str(response.content)
    try:
        return _extract_json(text)
    except Exception:
        # One repair-style retry, mirroring llm_router.complete()'s pattern:
        # ask the same model to convert its own output to valid JSON.
        repair = llm.invoke(
            [
                HumanMessage(
                    content="Convert the following into valid JSON only, no "
                    "prose, no code fences:\n\n" + text
                )
            ]
        )
        repair_text = repair.content if isinstance(repair.content, str) else str(repair.content)
        return _extract_json(repair_text)  # raises -> caller treats as a step failure


def _find_element(driver, target: dict):
    from appium.webdriver.common.appiumby import AppiumBy

    resource_id = (target or {}).get("resource_id")
    accessibility_id = (target or {}).get("accessibility_id")
    if resource_id:
        return driver.find_element(AppiumBy.ID, resource_id)
    if accessibility_id:
        return driver.find_element(AppiumBy.ACCESSIBILITY_ID, accessibility_id)
    raise ValueError("Brain returned an action with no resource_id or accessibility_id target")


def _substitute_credentials(value: Optional[str], sensitive_data: Optional[dict]) -> Optional[str]:
    """Swap a <cred:xxx> placeholder for its real value at the last possible
    moment — the plaintext value must never exist any earlier than the
    actual Appium send_keys() call."""
    if not value or not sensitive_data:
        return value
    if value.startswith(_PLACEHOLDER_PREFIX) and value.endswith(">"):
        key = value[len(_PLACEHOLDER_PREFIX):-1]
        return sensitive_data.get(key, value)
    return value


def _element_bounds_pct(element, window_size: dict) -> Optional[dict]:
    try:
        rect = element.rect
        w = window_size.get("width") or 1
        h = window_size.get("height") or 1
        return {
            "x_pct": 100.0 * rect["x"] / w,
            "y_pct": 100.0 * rect["y"] / h,
            "w_pct": 100.0 * rect["width"] / w,
            "h_pct": 100.0 * rect["height"] / h,
        }
    except Exception:
        return None


def _crop_and_diff(before_b64: str, after_b64: str, bounds_pct: Optional[dict]) -> Optional[float]:
    """Pixel-diff the target element's bounding box between two screenshots
    using the already-installed pixelmatch dependency, returning a 0..1
    mismatch ratio. None on any failure (missing bounds, decode error) — a
    guardrail that can't compute must never crash the run, only skip itself."""
    if not bounds_pct:
        return None
    try:
        from PIL import Image
        from pixelmatch.contrib.PIL import pixelmatch

        before_img = Image.open(io.BytesIO(base64.b64decode(before_b64))).convert("RGB")
        after_img = Image.open(io.BytesIO(base64.b64decode(after_b64))).convert("RGB")
        w, h = before_img.size
        box = (
            int(bounds_pct["x_pct"] / 100 * w),
            int(bounds_pct["y_pct"] / 100 * h),
            int((bounds_pct["x_pct"] + bounds_pct["w_pct"]) / 100 * w),
            int((bounds_pct["y_pct"] + bounds_pct["h_pct"]) / 100 * h),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        before_crop = before_img.crop(box)
        after_crop = after_img.crop(box).resize(before_crop.size)
        diff_img = Image.new("RGB", before_crop.size)
        mismatched = pixelmatch(before_crop, after_crop, diff_img, threshold=0.1)
        total = before_crop.size[0] * before_crop.size[1]
        return (mismatched / total) if total else None
    except Exception:
        logger.exception("Tap-verification pixel-diff failed (skipping this check)")
        return None


def run_android_test_sync(
    goal: str,
    farm_app_id: str,
    device_profile: Optional[str] = None,
    sensitive_data: Optional[dict] = None,
    max_steps: int = 25,
    max_duration_s: int = 480,
    on_event: Optional[Callable[[dict], None]] = None,
    llm_override: Optional[object] = None,
) -> dict:
    """Synchronous entry point for the Celery task's Android path.

    Returns {status, summary, events, failing_step, history_json,
    platform_metadata} — the same shape ai_runner.run_ai_test_sync()
    returns, so app.workers.tasks.ai_execution._persist_result() needs no
    platform-specific branching beyond reading platform_metadata.
    history_json is always None: there is no Android replay yet (Appium has
    no equivalent to browser_use.Agent.rerun_history()) —
    _maybe_save_skill() already no-ops correctly on this.

    max_steps (default 25) and max_duration_s (default 480 = 8 minutes) are
    tighter than ai_runner's web defaults (30 / 600) because a BrowserStack
    session is billed per minute — deliberately conservative, tune via
    ANDROID_MAX_STEPS/ANDROID_MAX_DURATION_S. max_duration_s must stay well
    under Celery's global task_soft_time_limit (1800s, see
    app/workers/celery_app.py) so this function's own driver.quit() always
    runs before Celery force-kills the task.
    """
    from app.services import device_farm

    device_profile = device_profile or device_farm.DEFAULT_DEVICE_PROFILE
    profile = device_farm.DEVICE_PROFILES.get(device_profile) or device_farm.DEVICE_PROFILES[
        device_farm.DEFAULT_DEVICE_PROFILE
    ]

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
        highlighted_element: Optional[dict] = None,
    ) -> dict:
        seq_counter["n"] += 1
        ev = {
            "sequence": seq_counter["n"],
            "status": status,
            "description": description,
            "step_type": step_type,
            "elapsed_ms": elapsed_ms(),
            "screenshot_url": screenshot_url,
            "highlighted_element": highlighted_element,
            "is_failing_step": is_failing,
        }
        events.append(ev)
        _notify(ev)
        return ev

    def _update(ev: dict, **changes) -> None:
        ev.update(changes)
        _notify(ev)

    def _shot(driver) -> tuple[Optional[str], Optional[str]]:
        try:
            b64 = driver.get_screenshot_as_base64()
            return f"data:image/png;base64,{b64}", b64
        except Exception:
            return None, None

    driver = None
    session_id: Optional[str] = None
    status_out = "inconclusive"
    summary_out = "Run did not complete."
    failing_step: Optional[dict] = None

    try:
        # ── Step: launch app on the device farm ──────────────────────────
        launch_event = _emit("deterministic", f"Launch app on {profile['label']}")
        try:
            from appium import webdriver as appium_webdriver
            from appium.options.common import AppiumOptions

            capabilities = device_farm.build_capabilities(
                farm_app_id, device_profile, session_name=goal[:100]
            )
            options = AppiumOptions()
            options.load_capabilities(capabilities)
            driver = appium_webdriver.Remote(
                command_executor=device_farm.remote_url(), options=options
            )
            session_id = driver.session_id
            shot_url, _ = _shot(driver)
            _update(launch_event, status="passed", elapsed_ms=elapsed_ms(), screenshot_url=shot_url)
        except Exception as exc:
            logger.exception("Failed to start Appium session")
            _update(launch_event, status="failed", elapsed_ms=elapsed_ms(), is_failing_step=True)
            status_out = "failed"
            summary_out = f"Could not start the device session: {exc}"
            failing_step = launch_event
            driver = None  # nothing to quit

        if driver is not None:
            expected_package = None
            try:
                expected_package = driver.current_package
            except Exception:
                logger.warning(
                    "Could not read current_package after launch — "
                    "allowed-package guardrail disabled for this run"
                )

            from app.services import ai_runner

            llm = None
            try:
                llm = llm_override or ai_runner._build_llm()
            except RuntimeError as exc:
                fail_event = _emit("ai_scoped", str(exc), status="failed", is_failing=True)
                status_out, summary_out, failing_step = "failed", str(exc), fail_event

            if llm is not None:
                history: list[str] = []
                recent_actions: list[tuple] = []
                deadline = start_ts + max_duration_s
                window_size = driver.get_window_size()

                for step_n in range(1, max_steps + 1):
                    if time.monotonic() >= deadline:
                        status_out = "failed"
                        summary_out = (
                            f"Agent timed out after {max_duration_s}s "
                            f"(max_steps={max_steps})."
                        )
                        break

                    try:
                        page_source = _truncate_page_source(driver.page_source)
                    except Exception as exc:
                        fail_event = _emit(
                            "ai_scoped",
                            f"Could not read screen state: {exc}",
                            status="failed",
                            is_failing=True,
                        )
                        failing_step = fail_event
                        status_out = "failed"
                        summary_out = f"Could not read screen state: {exc}"
                        break

                    try:
                        action = _ask_brain(llm, goal, page_source, history)
                    except Exception as exc:
                        fail_event = _emit(
                            "ai_scoped",
                            f"Brain could not decide the next action: {exc}",
                            status="failed",
                            is_failing=True,
                        )
                        failing_step = fail_event
                        status_out = "failed"
                        summary_out = f"Brain could not decide the next action: {exc}"
                        break

                    act = (action or {}).get("action")
                    target = (action or {}).get("target") or {}
                    value = (action or {}).get("value")
                    reasoning = (action or {}).get("reasoning") or f"Step {step_n}"

                    if act == "done":
                        history.append(f"{step_n}. done — {reasoning}")
                        _emit("ai_scoped", reasoning, status="passed")
                        status_out = "passed"
                        summary_out = reasoning or "Agent completed the goal."
                        break

                    if act == "fail":
                        fail_event = _emit("ai_scoped", reasoning, status="failed", is_failing=True)
                        failing_step = fail_event
                        status_out = "failed"
                        summary_out = reasoning or "Agent determined the goal cannot be completed."
                        break

                    # Stuck detector: same action on the same target
                    # repeating with no visible effect wastes farm minutes
                    # on a non-progressing loop.
                    action_key = (
                        act,
                        target.get("resource_id") or target.get("accessibility_id"),
                    )
                    recent_actions.append(action_key)
                    if len(recent_actions) > _STUCK_REPEAT_THRESHOLD:
                        recent_actions.pop(0)
                    if (
                        len(recent_actions) == _STUCK_REPEAT_THRESHOLD
                        and len(set(recent_actions)) == 1
                        and act in ("tap", "swipe", "input")
                    ):
                        fail_event = _emit(
                            "ai_scoped",
                            f"Stuck: '{act}' on the same target repeated "
                            f"{_STUCK_REPEAT_THRESHOLD}x with no progress — "
                            "halting for review.",
                            status="failed",
                            is_failing=True,
                        )
                        failing_step = fail_event
                        status_out = "inconclusive"
                        summary_out = "Run halted: stuck detector tripped."
                        break

                    if act == "assert":
                        ok = bool(value) and value.lower() in page_source.lower()
                        history.append(
                            f"{step_n}. assert '{value}' -> "
                            f"{'passed' if ok else 'failed'}"
                        )
                        assert_event = _emit(
                            "ai_scoped", reasoning,
                            status="passed" if ok else "failed", is_failing=not ok,
                        )
                        if not ok:
                            failing_step = assert_event
                            status_out = "failed"
                            summary_out = f"Assertion failed: {reasoning}"
                            break
                        continue

                    if act not in ("tap", "swipe", "input"):
                        fail_event = _emit(
                            "ai_scoped",
                            f"Brain returned an unknown action: {act!r}",
                            status="failed",
                            is_failing=True,
                        )
                        failing_step = fail_event
                        status_out = "failed"
                        summary_out = f"Brain returned an unknown action: {act!r}"
                        break

                    before_shot_url, before_shot_b64 = _shot(driver)
                    step_event = _emit("ai_scoped", reasoning, screenshot_url=before_shot_url)

                    try:
                        element = None
                        bounds_pct = None
                        if act in ("tap", "input"):
                            element = _find_element(driver, target)
                            bounds_pct = _element_bounds_pct(element, window_size)

                        if act == "tap":
                            element.click()
                        elif act == "input":
                            real_value = _substitute_credentials(value, sensitive_data)
                            element.clear()
                            element.send_keys(real_value or "")
                        elif act == "swipe":
                            direction = (value or "up").lower()
                            driver.execute_script(
                                "mobile: swipeGesture",
                                {
                                    "left": 0,
                                    "top": 0,
                                    "width": window_size["width"],
                                    "height": window_size["height"],
                                    "direction": direction,
                                    "percent": 0.75,
                                },
                            )

                        after_shot_url, after_shot_b64 = _shot(driver)

                        is_suspicious = False
                        if act == "tap" and before_shot_b64 and after_shot_b64:
                            diff_ratio = _crop_and_diff(before_shot_b64, after_shot_b64, bounds_pct)
                            if diff_ratio is not None and diff_ratio < _TAP_DIFF_MIN_RATIO:
                                is_suspicious = True

                        label = target.get("resource_id") or target.get("accessibility_id") or act
                        _update(
                            step_event,
                            status="passed",
                            elapsed_ms=elapsed_ms(),
                            screenshot_url=after_shot_url or before_shot_url,
                            highlighted_element=(
                                {**bounds_pct, "label": label} if bounds_pct else None
                            ),
                            is_failing_step=is_suspicious,
                        )
                        history.append(
                            f"{step_n}. {act} on {label}"
                            f"{' = ' + value if act == 'input' and value else ''} "
                            f"— {reasoning}"
                            f"{' [WARNING: no visible change detected]' if is_suspicious else ''}"
                        )

                        # Allowed-package guardrail — Android's analog of
                        # ai_runner.py's allowed_domains.
                        if expected_package:
                            try:
                                if driver.current_package != expected_package:
                                    fail_event = _emit(
                                        "ai_scoped",
                                        "Navigated outside the app under test "
                                        f"(now in {driver.current_package}) — halting.",
                                        status="failed",
                                        is_failing=True,
                                    )
                                    failing_step = fail_event
                                    status_out = "failed"
                                    summary_out = "Agent navigated outside the app under test."
                                    break
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.exception("Action execution failed at step %d", step_n)
                        _update(
                            step_event, status="failed", elapsed_ms=elapsed_ms(),
                            is_failing_step=True,
                        )
                        failing_step = step_event
                        status_out = "failed"
                        summary_out = f"Step {step_n} ({act}) failed: {exc}"
                        break
                else:
                    # Loop exhausted max_steps without done/fail/error/stuck.
                    status_out = "inconclusive"
                    summary_out = f"Agent did not finish the goal within {max_steps} steps."

            # ── Step: deterministic final capture ─────────────────────────
            verify_event = _emit("deterministic", "Capture final state and evaluate outcome")
            final_shot_url, _ = _shot(driver)
            _update(verify_event, status="passed", elapsed_ms=elapsed_ms(), screenshot_url=final_shot_url)
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logger.exception("Failed to quit Appium driver cleanly")

    platform_metadata = None
    if session_id:
        details = device_farm.get_session_details(session_id)
        if details:
            platform_metadata = {
                "farm_vendor": "browserstack",
                "farm_session_id": session_id,
                **details,
            }

    return {
        "status": status_out,
        "summary": summary_out,
        "events": events,
        "failing_step": failing_step,
        "history_json": None,
        "platform_metadata": platform_metadata,
    }
