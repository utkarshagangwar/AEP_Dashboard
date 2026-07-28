#!/usr/bin/env python3
"""Golden regression set runner for the New Vibe Test AI pipeline itself.

New Vibe Test Phase 7 (F.25). Per explicit product decision (2026-07-28,
"Scaffolding only, run manually"): this is intentionally NOT wired into CI,
has NO scheduled/automatic trigger, and adds NO recurring cost. It exists so
a human can, on demand, ask "did I just make the AI worse?" after touching
the pieces that decide pass/fail/needs_review for every run in production --
not "did AEP_Dashboard's app break" (that's what the golden cases' own
target app is a fixed, stable stand-in for).

WHEN TO RUN THIS (manually, from a terminal with real credentials/network):
  - After changing app/services/ai_runner.py (the agent's action-planning
    loop / step execution)
  - After changing app/services/ai_eval.py (the GEval judge, its threshold,
    or its prompt template)
  - After changing app/services/visual_judge.py (pixel-diff or vision pass)
  - After changing any message/context/system-prompt guidance text fed to
    the agent or the judges
  - After bumping the deepeval version or swapping/adding an LLM provider
    in llm_router.py
A meaningful score/verdict drift on a golden case that used to be stable is
the signal to investigate BEFORE shipping the change, not after.

WHAT THIS DOES NOT DO:
  - Does not touch the database, Celery, or any AITestRun/VisualRun row --
    completely out-of-band from the real pipeline's persistence.
  - Does not retry, does not gate deploys, does not post anywhere. It is a
    read-only diagnostic you run in your own terminal and read yourself.
  - Requires real LLM API keys, network access, and a working Chromium
    install -- the exact same requirements as a live run. There is no mock
    mode; a golden set that doesn't exercise the real pipeline can't catch
    real regressions in it.

USAGE:
    cd backend
    python3 golden_tests/run_golden_set.py                 # run everything
    python3 golden_tests/run_golden_set.py --only functional
    python3 golden_tests/run_golden_set.py --only ui
    python3 golden_tests/run_golden_set.py --case-id func-login-happy

Exit code is non-zero if any case's actual outcome didn't match its
expected outcome in golden_set.json -- suitable for `&& echo ok` style
manual gating, but again: nothing runs this automatically.

DESIGN NOTE (why the scoring/comparison logic below is split out as pure
functions): the functions prefixed `_gate_` and `_score_` take already-
computed results (a run-result dict, an eval-result dict, a judge verdict)
and return a plain outcome -- they do not themselves call the LLM, launch
a browser, or hit the network. This lets the decision logic (which mirrors
app/workers/tasks/ai_execution.py's real gating exactly -- see
_gate_functional_status below) be verified with mocked inputs in a fast,
offline unit test, independent of whether a live run was actually
exercised. The `_run_*_case` functions above them are the thin, deliberately
un-unit-tested glue that calls the real pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_HERE)
_DEFAULT_FIXTURE_PATH = os.path.join(_HERE, "golden_set.json")


@dataclass
class CaseOutcome:
    case_id: str
    kind: str  # "functional" | "ui"
    expected: str
    actual: str
    passed: bool  # did actual match expected -- i.e. no regression
    detail: str = ""


# ── Pure decision logic (unit-testable without a live run) ──────────────────

def _gate_functional_status(
    agent_status: str,
    eval_result: Optional[dict],
    threshold: float,
) -> str:
    """Mirrors app/workers/tasks/ai_execution.py::_persist_result's gating
    EXACTLY (same asymmetric rule: a low GEval score can only downgrade an
    agent-reported "passed" to "needs_review", never upgrade a "failed", and
    a missing eval_result -- scoring unavailable -- leaves the agent's own
    status untouched). Keep this in sync with that function if the real
    gating rule ever changes; that's the whole point of this harness."""
    final_status = agent_status
    if final_status == "passed" and eval_result is not None:
        if eval_result["score"] < threshold:
            final_status = "needs_review"
    return final_status


def _score_functional_case(case: dict, run_result: dict, eval_result: Optional[dict], threshold: float) -> CaseOutcome:
    agent_status = run_result.get("status", "failed")
    actual = _gate_functional_status(agent_status, eval_result, threshold)
    expected = case["expected_status"]
    score_note = f", eval_score={eval_result['score']:.3f}" if eval_result else ", eval_score=n/a"
    return CaseOutcome(
        case_id=case["id"],
        kind="functional",
        expected=expected,
        actual=actual,
        passed=(actual == expected),
        detail=f"agent_status={agent_status}{score_note}",
    )


def _gate_visual_status(
    pixel_mismatch_pct: float,
    findings: list[dict],
    vision_available: bool,
    vision_skipped: bool,
    fail_mismatch_pct: float,
) -> str:
    """Mirrors app/workers/tasks/visual_audit.py::run_visual_audit_task's
    post-judge status decision EXACTLY: fail on a serious finding or
    mismatch above budget; "partial" if the vision pass genuinely
    couldn't run (not a deliberate self-execution skip); else pass. Keep in
    sync with that function if the real rule ever changes."""
    has_serious = any(f.get("severity") in ("critical", "major") for f in findings)
    if pixel_mismatch_pct > fail_mismatch_pct or has_serious:
        return "failed"
    if not vision_available and not vision_skipped:
        return "partial"
    return "passed"


def _score_ui_case(case: dict, verdict, fail_mismatch_pct: float) -> CaseOutcome:
    actual = _gate_visual_status(
        verdict.pixel_mismatch_pct,
        verdict.findings,
        verdict.vision_available,
        verdict.vision_skipped,
        fail_mismatch_pct,
    )
    expected = case["expected_verdict_status"]
    max_allowed = case.get("max_mismatch_pct")
    # max_mismatch_pct in the fixture is a stricter, case-specific budget on
    # top of the global VISUAL_FAIL_MISMATCH_PCT gate above -- e.g. a golden
    # case for a pixel-perfect page might want a tighter bound than the
    # platform-wide default.
    if actual == "passed" and max_allowed is not None and verdict.pixel_mismatch_pct > max_allowed:
        actual = "failed"
    detail = f"pixel_mismatch_pct={verdict.pixel_mismatch_pct}, findings={len(verdict.findings)}"
    if max_allowed is not None:
        detail += f", case_max_mismatch_pct={max_allowed}"
    return CaseOutcome(
        case_id=case["id"],
        kind="ui",
        expected=expected,
        actual=actual,
        passed=(actual == expected),
        detail=detail,
    )


# ── Live glue (calls the real pipeline; not exercised by unit tests) ────────

def _run_functional_case(case: dict) -> CaseOutcome:
    # Deferred imports: this module is imported by the (fast, offline) unit
    # tests too, and the real ai_runner/ai_eval modules pull in deepeval,
    # browser-use, an LLM SDK chain, etc. -- heavy, and unnecessary for
    # anything that only exercises the pure _score_/_gate_ functions above.
    from app.services.ai_runner import run_ai_test_sync
    from app.services.ai_eval import evaluate_run, get_eval_threshold

    run_result = run_ai_test_sync(
        goal=case["goal"],
        environment_url=case.get("environment_url", "about:blank"),
        allowed_domains=case.get("allowed_domains"),
        sensitive_data=case.get("sensitive_data") or {},
    )
    eval_result = evaluate_run(
        goal=case["goal"],
        events=run_result.get("events", []),
        summary=run_result.get("summary"),
    )
    return _score_functional_case(case, run_result, eval_result, get_eval_threshold())


def _run_ui_case(case: dict) -> CaseOutcome:
    import tempfile
    from app.workers.tasks.visual_audit import _capture_screenshot
    from app.services.visual_judge import judge

    reference_path = os.path.join(_BACKEND_ROOT, case["reference_image_path"])
    if not os.path.exists(reference_path):
        return CaseOutcome(
            case_id=case["id"],
            kind="ui",
            expected=case["expected_verdict_status"],
            actual="error",
            passed=False,
            detail=f"reference image not found at {reference_path} -- see golden_set.json's "
                    f"template notes for how to capture one",
        )

    fail_mismatch_pct = float(os.environ.get("VISUAL_FAIL_MISMATCH_PCT", 1.0))
    with tempfile.TemporaryDirectory(prefix="golden_ui_") as tmp:
        screenshot_path = os.path.join(tmp, "screenshot.png")
        diff_path = os.path.join(tmp, "diff.png")
        _capture_screenshot(case["target_url"], screenshot_path)
        verdict = judge(reference_path, screenshot_path, diff_path)
        return _score_ui_case(case, verdict, fail_mismatch_pct)


# ── Report + CLI ──────────────────────────────────────────────────────────

def _print_report(outcomes: list[CaseOutcome]) -> bool:
    """Returns True iff every case matched its expected outcome."""
    all_ok = True
    print(f"{'CASE':<28} {'KIND':<11} {'EXPECTED':<14} {'ACTUAL':<14} {'RESULT':<8} DETAIL")
    print("-" * 110)
    for o in outcomes:
        result = "OK" if o.passed else "REGRESSION"
        if not o.passed:
            all_ok = False
        print(f"{o.case_id:<28} {o.kind:<11} {o.expected:<14} {o.actual:<14} {result:<8} {o.detail}")
    print("-" * 110)
    n = len(outcomes)
    n_ok = sum(1 for o in outcomes if o.passed)
    print(f"{n_ok}/{n} cases matched expected outcome.")
    if not all_ok:
        print("\nOne or more golden cases regressed. Investigate before shipping the "
              "change that triggered this run -- see this script's module docstring "
              "for what usually causes each kind of drift.")
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", default=_DEFAULT_FIXTURE_PATH, help="Path to golden_set.json")
    parser.add_argument("--only", choices=["functional", "ui"], default=None, help="Run only one kind of case")
    parser.add_argument("--case-id", default=None, help="Run only the case with this id")
    args = parser.parse_args()

    with open(args.fixtures, "r", encoding="utf-8") as f:
        fixtures = json.load(f)

    cases: list[tuple[str, dict]] = []
    if args.only in (None, "functional"):
        cases += [("functional", c) for c in fixtures.get("functional_cases", [])]
    if args.only in (None, "ui"):
        cases += [("ui", c) for c in fixtures.get("ui_cases", [])]
    if args.case_id:
        cases = [(kind, c) for kind, c in cases if c["id"] == args.case_id]

    if not cases:
        print("No matching cases to run (check --only / --case-id / golden_set.json).")
        return 1

    outcomes: list[CaseOutcome] = []
    for kind, case in cases:
        print(f"Running {kind} case {case['id']!r} ...", file=sys.stderr)
        try:
            if kind == "functional":
                outcomes.append(_run_functional_case(case))
            else:
                outcomes.append(_run_ui_case(case))
        except Exception as exc:  # noqa: BLE001 -- report as a failed case, don't crash the whole batch
            outcomes.append(CaseOutcome(
                case_id=case["id"],
                kind=kind,
                expected=case.get("expected_status") or case.get("expected_verdict_status", "?"),
                actual="error",
                passed=False,
                detail=f"exception during run: {exc!r}",
            ))

    all_ok = _print_report(outcomes)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
