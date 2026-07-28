"""Vibe Testing quality scoring -- DeepEval (GEval), post-run only.

Scores how well a finished New Vibe Test / Skill Replay run actually
accomplished its stated goal, as a second opinion independent of the
agent's own self-reported success/fail. One extra LLM call after the run
has already finished -- same "post-run, best-effort, never blocks
persistence" contract as ai_runner.generate_narrative_summary, and the
same "AI work must not run inside/concurrently with the live run" spirit
applied elsewhere in this codebase's automation suites: this module is
only ever called from app.workers.tasks.ai_execution._persist_result,
strictly after the browser/agent has already finished and torn down.

Scope: New Vibe Test ("ai") and Skill Replay ("skill_replay") runs, on
either platform (Phase 4, 2026-07-28: opened up to Android -- see D.16 below).
Autonomous QA (app.services.orchestrator) has its own separate persistence
path and never calls this. Any non-terminal (inconclusive/cancelled) status
is excluded by the caller before this module is even imported -- see
_persist_result's gating.

Android eligibility (D.16): app.services.android_runner.run_android_test_sync
returns the exact same {status, summary, events, ...} shape as
ai_runner.run_ai_test_sync(), and its events already use step_type=
"ai_scoped"/status/description identically to the web path (confirmed by
direct inspection of android_runner.py, whose own docstring guarantees this)
-- so this module needed zero Android-specific code to become usable there.
The previous "web platform only" gate in _persist_result was more
conservative than the actual data shape required.

Gating scope (D.15, per explicit product decision 2026-07-28): the
needs_review gate _persist_result derives from this module's score applies
to every run this module scores -- not only rows created via the
structured Functional Test flow (test_category="functional") -- so a
legacy free-text goal run or a Skill Replay rerun can also be gated, same
as a Functional Test row.

Why GEval and not TaskCompletionMetric: DeepEval's TaskCompletionMetric
requires @observe()-based tracing around live execution and has no
supported standalone measure() path on a plain LLMTestCase (confirmed
against the installed deepeval==3.3.9) -- wiring tracing into the live
browser-use agent loop would be a much larger, more invasive change to
ai_runner.py's execution path than a post-run quality score justifies.
GEval runs standalone (async_mode=False forces a fully synchronous
measure() call, safe to call from inside a Celery task) against a plain
LLMTestCase built from data _persist_result already has on hand: the
goal, the agent's own step-by-step actions, and its closing summary.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger

logger = get_logger(__name__)

# Set before deepeval is ever imported (lazily, inside evaluate_run below):
# this project already loads its own .env (see app/core/config.py) and is
# an internal tool with no reason for a third-party eval library to also
# scan for .env files or phone home anonymous telemetry on every call.
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

METRIC_NAME = "vibe_test_goal_completion"

# Long Vibe Test goals can run for thousands of agent steps
# (ai_execution.py's _VIBE_TEST_MAX_STEPS = 100_000) -- capping how many
# step descriptions get embedded in the judge prompt keeps the eval call's
# cost/latency bounded the same way generate_narrative_summary does (and
# sow_ledger.py caps facts per call). Phase 3 (2026-07-28): this used to be
# a flat "first 60" cut -- see step_sampling.sample_steps, now shared with
# generate_narrative_summary, for the smarter first/last/anomaly window
# that replaced it. Phase 4 (2026-07-28): raised from 60 to
# step_sampling.DEFAULT_CAP (100) so this "independent" score is built from
# the exact same first-10/last-30/every-anomaly, 100-step budget the
# narrative summary already uses, instead of a narrower window that could
# see less of the run than the human-readable summary does.
# _MAX_STEP_CHARS (per-step truncation) is unchanged.
_MAX_STEP_CHARS = 300

# Default GEval pass threshold, overridable via VIBE_TEST_EVAL_THRESHOLD
# (Phase 4, D.17) -- matches this codebase's usual "constant default,
# env-configurable" convention (e.g. _VIBE_TEST_SOFT_TIME_LIMIT_S in
# ai_execution.py) instead of the previous hardcoded 0.5 in the GEval(...)
# constructor. Shared by app.workers.tasks.ai_execution._persist_result's
# needs_review gate, so the two can never drift out of sync with each
# other -- callers must import get_eval_threshold() rather than reading the
# env var themselves.
_DEFAULT_EVAL_THRESHOLD = 0.5


def get_eval_threshold() -> float:
    """Read VIBE_TEST_EVAL_THRESHOLD, clamped to (0, 1]. Falls back to
    _DEFAULT_EVAL_THRESHOLD on anything unset/unparseable/out of range --
    a bad deployment env value must never crash scoring or run persistence,
    only silently use the documented default (same "never raise" contract
    as the rest of this module)."""
    raw = os.environ.get("VIBE_TEST_EVAL_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_EVAL_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "VIBE_TEST_EVAL_THRESHOLD=%r is not a number, using default %.2f",
            raw, _DEFAULT_EVAL_THRESHOLD,
        )
        return _DEFAULT_EVAL_THRESHOLD
    if not (0.0 < value <= 1.0):
        logger.warning(
            "VIBE_TEST_EVAL_THRESHOLD=%r is outside (0, 1], using default %.2f",
            value, _DEFAULT_EVAL_THRESHOLD,
        )
        return _DEFAULT_EVAL_THRESHOLD
    return value


# Pass threshold for the SECOND judge (evaluate_expected_results below), used
# by _persist_result's expected-results gate. Separate constant and separate
# env var from _DEFAULT_EVAL_THRESHOLD on purpose: the two judges answer
# different questions and have very different error profiles, so one knob
# could not sensibly tune both.
#
# Why 0.5 and not 1.0, even though a Functional Test's own goal text says
# "the test only passes if ALL of these hold": _EXPECTED_RESULTS_SYSTEM
# deliberately instructs the model to answer "unconfirmed" whenever it cannot
# tell from the screenshot. That bias is correct for a judge (better to admit
# uncertainty than to rubber-stamp), but it means a strict 1.0 threshold
# would flag almost every real run -- an expected result about a toast that
# has already faded, content below the fold, or anything not visually
# checkable at all ("a confirmation email is sent") is unconfirmable by
# construction, not evidence of a defect. A run where the feature genuinely
# did nothing scores at or near 0, so a mid threshold still catches the
# failure mode this gate exists for without drowning real passes in review.
_DEFAULT_VISUAL_EVAL_THRESHOLD = 0.5


def get_visual_eval_threshold() -> float:
    """Read VIBE_TEST_VISUAL_EVAL_THRESHOLD, clamped to (0, 1]. Same
    never-raise contract and fallback behaviour as get_eval_threshold():
    a bad deployment value must silently use the documented default rather
    than crash scoring or run persistence. Callers must import this rather
    than reading the env var themselves, so the gate and the score can never
    drift apart."""
    raw = os.environ.get("VIBE_TEST_VISUAL_EVAL_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_VISUAL_EVAL_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "VIBE_TEST_VISUAL_EVAL_THRESHOLD=%r is not a number, using default %.2f",
            raw, _DEFAULT_VISUAL_EVAL_THRESHOLD,
        )
        return _DEFAULT_VISUAL_EVAL_THRESHOLD
    if not (0.0 < value <= 1.0):
        logger.warning(
            "VIBE_TEST_VISUAL_EVAL_THRESHOLD=%r is outside (0, 1], using default %.2f",
            value, _DEFAULT_VISUAL_EVAL_THRESHOLD,
        )
        return _DEFAULT_VISUAL_EVAL_THRESHOLD
    return value


# ── D.19: the actual final judge prompt, documented in-repo ─────────────────
#
# DeepEval's GEval never exposes its prompt as a public API -- it's built at
# call time inside the pinned deepeval==3.3.9 package (from
# deepeval/metrics/g_eval/template.py's GEvalTemplate.generate_evaluation_results,
# GEval.async_mode=False + strict_mode=False path, confirmed directly against
# the installed wheel for this exact pinned version on 2026-07-28). Copied
# here verbatim (not executed -- this string is never imported or called;
# it exists purely so the full prompt chain can be audited in this repo
# without re-inspecting a third-party package) so a reviewer can see exactly
# what the judge model is asked, with {evaluation_steps} being this file's
# own _EVAL_STEPS (numbered 1-6 by DeepEval) and {test_case_content} being
# LLMTestCase's input/actual_output/context rendered as
# "Input:\n...\n\nActual Output:\n...\n\nContext:\n...". score_range
# defaults to (0, 10) (no rubric is configured here) -- GEval then divides
# the judge's raw integer score by 10 to produce the 0-1 float this module
# stores as eval_score.
#
# If deepeval is ever upgraded, re-diff this string against the new
# package's template.py before trusting it as documentation again --
# nothing enforces that these stay in sync automatically.
_DEEPEVAL_GEVAL_JUDGE_PROMPT_TEMPLATE_v3_3_9 = '''\
You are an evaluator. Given the following evaluation steps, assess the response below and return a JSON object with two fields:

- "score": an integer between 0 and 10, with 10 indicating strong alignment with the evaluation steps and 0 indicating no alignment.
- "reason": a brief explanation for why the score was given. This must mention specific strengths or shortcomings, referencing relevant details from the input. Do not quote the score itself in the explanation.

Your explanation should:
- Be specific and grounded in the evaluation steps.
- Mention key details from the test case parameters.
- Be concise, clear, and focused on the evaluation logic.

Only return valid JSON. Do not include any extra commentary or text.

---

Evaluation Steps:
{evaluation_steps}

Test Case:
{test_case_content}

Parameters:
{parameters}

---
Example JSON:
{{
    "reason": "your concise and informative reason here",
    "score": 0
}}

JSON:
'''

_EVAL_STEPS = [
    "'input' is the natural-language goal a browser automation agent was asked to accomplish.",
    "'context' lists the individual actions the agent actually took, in the order it took them, as recorded by the automation framework -- treat this as ground truth of what happened, not a claim.",
    "'actual_output' is the agent's own closing summary of what it did.",
    "Judge whether the actions listed in 'context' genuinely accomplish the goal in 'input' -- do not simply trust the claim in 'actual_output'.",
    "Heavily penalize vague, generic, or unsupported claims of success that are not backed by a concrete matching action in 'context'.",
    "A goal that was only partially completed, abandoned partway through, or 'completed' via an unintended workaround should score lower than one fully and correctly completed.",
]


def evaluate_run(
    *,
    goal: str,
    events: list[dict],
    summary: str | None,
    run_id: str | None = None,
) -> dict | None:
    """Return {"score": float 0-1, "reason": str|None, "metric": str}, or
    None if scoring wasn't possible for any reason (deepeval not
    installed, no ai_scoped steps to judge, every model in the router
    chain failed, or the judge's response couldn't be parsed). Never
    raises -- every failure mode is caught and logged here so the caller
    can treat this as pure best-effort, matching
    generate_narrative_summary's contract exactly.

    run_id: for the truncation log line only (see step_sampling.DEFAULT_CAP
    above) -- optional, purely cosmetic.
    """
    try:
        from deepeval.metrics import GEval
        from deepeval.models.base_model import DeepEvalBaseLLM
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams

        from app.services.llm_router import complete as llm_complete

        class _RouterBackedModel(DeepEvalBaseLLM):
            """Routes GEval's judge calls through this project's existing
            llm_router (same primary->fallback chain, retries, and AI
            Usage cost logging already used by narrative summaries and
            the visual Judge) instead of deepeval's own OpenAI-only
            default judge -- so this feature needs no separate
            OPENAI_API_KEY and every call it makes is visible on the
            existing AI Usage page like any other LLM call in this app.

            Phase 4 (D.18, 2026-07-28): generate() now accepts GEval's
            `schema` kwarg instead of deliberately raising TypeError on it.
            GEval's non-native-model path (deepeval/metrics/g_eval/g_eval.py,
            re-verified against the exact pinned deepeval==3.3.9 by direct
            source inspection on this date) first tries
            self.model.generate_raw_response(...) for log-prob-weighted
            scoring -- undefined here, so Python raises AttributeError,
            which GEval catches -- then calls
            self.model.generate(prompt, schema=ReasonScore) expecting a
            schema INSTANCE back. Previously this class omitted the `schema`
            param so that call raised TypeError, which GEval also catches,
            falling back to a second plain generate(prompt) call and its own
            internal trimAndLoadJson text-scraping to pull out score/reason
            -- fragile, and explicitly documented as such before this phase.
            Now: when schema is given, this method itself asks llm_router
            for strict JSON (expect_json=True, which already retries once
            with a repair prompt on invalid JSON -- see llm_router.complete)
            and validates the result into a real `schema(...)` instance,
            which GEval then reads .score/.reason off of directly with no
            text-scraping of its own. generate_raw_response is still
            deliberately left undefined -- implementing real log-prob
            weighted scoring would require every provider in the AXON/
            Google/OpenRouter chain to expose token log-probabilities
            through litellm uniformly, which is not verified and out of
            scope for this fix.
            """

            def generate(self, prompt: str, schema=None):
                if schema is not None:
                    result = llm_complete(
                        prompt, max_tokens=1024, temperature=0.0, expect_json=True
                    )
                    return schema(**result.parsed_json)
                result = llm_complete(prompt, max_tokens=1024, temperature=0.0)
                return result.text

            def load_model(self):
                return self

            async def a_generate(self, prompt: str, schema=None):
                return self.generate(prompt, schema=schema)

            def get_model_name(self) -> str:
                return "aep-llm-router"

        from app.services.step_sampling import DEFAULT_CAP, sample_steps, truncation_marker

        ai_scoped_events = [ev for ev in events if ev.get("step_type") == "ai_scoped"]
        sampled_events, was_truncated = sample_steps(
            ai_scoped_events, cap=DEFAULT_CAP
        )
        if was_truncated:
            logger.warning(
                "Vibe Test quality scoring for run_id=%s: %s",
                run_id, truncation_marker(len(ai_scoped_events), len(sampled_events)),
            )

        ai_steps = [
            (ev.get("description") or "").strip().replace("\n", " ")[:_MAX_STEP_CHARS]
            for ev in sampled_events
        ]
        ai_steps = [s for s in ai_steps if s]
        if not ai_steps:
            # Nothing the agent actually did to judge (e.g. it failed before
            # taking any real action) -- no meaningful score to produce.
            return None
        if was_truncated:
            ai_steps.insert(
                0, truncation_marker(len(ai_scoped_events), len(sampled_events))
            )

        test_case = LLMTestCase(
            input=goal,
            actual_output=(summary or "").strip() or "(agent reported no closing summary)",
            context=ai_steps,
        )
        metric = GEval(
            name="Vibe Test Goal Completion",
            evaluation_steps=_EVAL_STEPS,
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.CONTEXT,
            ],
            model=_RouterBackedModel(),
            threshold=get_eval_threshold(),
            # Forces GEval's fully synchronous code path (no event loop /
            # asyncio.run() involved) -- safe to call from inside a Celery
            # task, which has no event loop of its own running. Verified
            # directly: with async_mode=False, measure() never touches
            # asyncio at all.
            async_mode=False,
        )
        metric.measure(test_case)

        return {
            "score": round(float(metric.score), 3),
            "reason": (metric.reason or "").strip() or None,
            "metric": METRIC_NAME,
        }
    except Exception as exc:
        logger.warning(
            "Vibe Test quality scoring failed (run persisted normally without a score): %s",
            exc,
        )
        return None


# ── Second, complementary judge pass: final-state screenshot vs. expected
#    results (Phase 4 checklist bullet 7, Functional Test only) ─────────────
#
# evaluate_run() above asks "did the agent's actions, in sequence, genuinely
# accomplish the goal" -- an action-trace judge. This asks a different
# question: "does the final screen actually show what the test's own
# `expected results` said it should" -- an end-state judge. The two catch
# different failure modes: an agent can take every action a human would call
# "correct" and still land on a page that silently didn't do what was
# expected (e.g. a form POST that 200s and navigates forward but the record
# was never actually created) -- action-trace judging alone has no way to
# notice that, but a screenshot of the final page compared against "a new
# record should appear in the list" can. Same visual-judge pattern already
# used by app/services/visual_judge.py's run_vision_pass (llm_router.complete
# with images_b64 + expect_json=True) rather than inventing a new one.
_EXPECTED_RESULTS_SYSTEM = (
    "You are a QA engineer verifying a completed browser automation test's "
    "final outcome. You are given the test's own list of expected results "
    "(what should be true once the test finished) and a screenshot of the "
    "actual final page state. For each expected result, decide whether the "
    "screenshot visually or textually confirms it -- do not guess at "
    "anything not actually visible in the image; if you cannot tell, treat "
    "it as unconfirmed rather than assuming success. Respond with JSON "
    "only: "
    '{"confirmed": [str], "unconfirmed": [str], "reason": str} '
    "where confirmed/unconfirmed together contain every expected result "
    "given, verbatim, partitioned by whether the screenshot supports it. "
    "reason is a brief, concrete explanation grounded in what's actually "
    "visible in the screenshot."
)


def evaluate_expected_results(
    *,
    expected_results: list[str],
    screenshot_b64: str,
    goal: str | None = None,
    run_id: str | None = None,
) -> dict | None:
    """Return {"score": float 0-1, "reason": str|None, "confirmed": [str],
    "unconfirmed": [str], "metric": str}, or None if there was nothing
    checkable (no expected_results, no screenshot) or the vision call
    failed for any reason. Never raises -- same best-effort contract as
    evaluate_run(); the caller (_persist_result) must never have run
    persistence blocked by this.

    score is confirmed-count / total, computed here (not asked of the
    model directly) so it can't drift from the confirmed/unconfirmed lists
    the model actually returned.
    """
    if not expected_results:
        return None
    if not screenshot_b64:
        logger.info(
            "Expected-results visual pass skipped for run_id=%s: no final "
            "screenshot available",
            run_id,
        )
        return None
    try:
        from app.services import llm_router

        clean_results = [str(r).strip() for r in expected_results if str(r or "").strip()]
        if not clean_results:
            return None

        prompt = (
            (f"Test goal:\n{goal}\n\n" if goal else "")
            + "Expected results to verify against the screenshot:\n"
            + "\n".join(f"- {r}" for r in clean_results)
        )
        result = llm_router.complete(
            prompt,
            system=_EXPECTED_RESULTS_SYSTEM,
            images_b64=[screenshot_b64],
            expect_json=True,
            max_tokens=1024,
        )
        raw = result.parsed_json or {}
        confirmed = [str(x).strip() for x in (raw.get("confirmed") or []) if str(x or "").strip()]
        unconfirmed = [str(x).strip() for x in (raw.get("unconfirmed") or []) if str(x or "").strip()]
        if not confirmed and not unconfirmed:
            # Model returned neither list populated -- nothing usable to
            # score, treat like any other unparseable/empty response.
            return None

        total = len(confirmed) + len(unconfirmed)
        score = round(len(confirmed) / total, 3) if total else 0.0

        return {
            "score": score,
            "reason": (str(raw.get("reason") or "").strip() or None),
            "confirmed": confirmed,
            "unconfirmed": unconfirmed,
            "metric": "vibe_test_expected_results_visual",
        }
    except Exception as exc:
        logger.warning(
            "Expected-results visual confirmation pass failed for "
            "run_id=%s (run persisted normally without this second "
            "score): %s",
            run_id, exc,
        )
        return None
