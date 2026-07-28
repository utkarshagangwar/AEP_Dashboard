"""Shared step-sampling for post-run AI summaries/scoring.

New Vibe Test Phase 3 (full-run observability). A Vibe Test run can have up
to _VIBE_TEST_MAX_STEPS (100,000) events. Both the narrative summary
(app.services.ai_runner.generate_narrative_summary) and the independent
quality score (app.services.ai_eval.evaluate_run) used to build their LLM
prompt from just the FIRST 60 recorded steps -- for a long run, if
something went wrong near the end (the most common place a multi-page
functional flow actually fails), neither one would ever see it: the
"failure" a QA engineer reads about in the summary, or that GEval scores,
could be entirely absent from what either call actually looked at.

sample_steps() replaces that flat first-N cutoff with a representative
window: always the first `first_n` steps (how the run started), always the
last `last_n` steps (how it ended -- where a failure usually shows up),
plus every step flagged as failed/anomalous wherever it falls in between,
deduplicated and re-sorted by original order, up to a total `cap`.

Deliberately has no dependency on ai_runner.py or ai_eval.py (a plain list
of event dicts in, a plain list back out) so either module can import this
without any import-order/circularity concerns.
"""
from __future__ import annotations

# Matches ai_runner.generate_narrative_summary's/ai_eval.evaluate_run's
# previous flat "first 60" cap -- same total budget, smarter selection.
DEFAULT_CAP = 100
DEFAULT_FIRST_N = 10
DEFAULT_LAST_N = 30


def _is_anomalous(event: dict) -> bool:
    """True for a step worth keeping regardless of where it falls in the
    run: an explicit failure/error status, or the failing-step flag some
    callers set independently of status (see AIRunEvent.is_failing_step)."""
    status = (event.get("status") or "").lower()
    if status in ("failed", "error"):
        return True
    if event.get("is_failing_step"):
        return True
    return False


def sample_steps(
    events: list[dict],
    *,
    first_n: int = DEFAULT_FIRST_N,
    last_n: int = DEFAULT_LAST_N,
    cap: int = DEFAULT_CAP,
) -> tuple[list[dict], bool]:
    """Return (sampled_events, was_truncated).

    sampled_events is a subsequence of `events`, in original order, never
    longer than `cap`. was_truncated is True whenever len(events) >
    len(sampled_events) -- i.e. the caller is looking at less than the
    full run and should say so (see both callers' "step log truncated: N
    of M steps shown" marker).

    Selection, in priority order (a step already selected is never
    double-counted against `cap`):
      1. every anomalous step (failed/error/is_failing_step) -- these are
         exactly the steps a reviewer most needs to see, and there's
         normally only a handful even in a very long run;
      2. the first `first_n` steps -- how the run started;
      3. the last `last_n` steps -- how the run ended, where a
         functional-flow failure most commonly surfaces;
      4. if there's still room under `cap` after 1-3, fill forward from
         wherever the run isn't yet covered, so a short/medium run that
         fits under `cap` anyway is returned whole (was_truncated=False),
         matching today's behavior exactly for every run under 100 steps.
    """
    n = len(events)
    if n <= cap:
        return list(events), False

    keep_idx: set[int] = set()

    for i, ev in enumerate(events):
        if _is_anomalous(ev):
            keep_idx.add(i)

    for i in range(min(first_n, n)):
        keep_idx.add(i)

    for i in range(max(0, n - last_n), n):
        keep_idx.add(i)

    if len(keep_idx) > cap:
        # Anomalies always win; first/last get trimmed evenly if even the
        # anomaly set alone doesn't leave room (pathological case: a run
        # with more than `cap` failed steps). Keep the earliest and latest
        # anomalies over ones in the middle -- same "start/end matter most"
        # bias as the rest of this function.
        anomaly_idx = sorted(i for i in keep_idx if _is_anomalous(events[i]))
        if len(anomaly_idx) >= cap:
            half = cap // 2
            keep_idx = set(anomaly_idx[:half]) | set(anomaly_idx[-(cap - half):])
        else:
            room = cap - len(anomaly_idx)
            non_anomaly = sorted(i for i in keep_idx if i not in anomaly_idx)
            half = room // 2
            trimmed = set(non_anomaly[:half]) | set(non_anomaly[-(room - half):])
            keep_idx = set(anomaly_idx) | trimmed
    else:
        # Room to spare under cap — fill forward from the start so the
        # window reads as one contiguous block wherever possible, instead
        # of a sparse handful of first/last/anomaly steps with gaps.
        remaining = cap - len(keep_idx)
        for i in range(n):
            if remaining <= 0:
                break
            if i not in keep_idx:
                keep_idx.add(i)
                remaining -= 1

    sampled = [events[i] for i in sorted(keep_idx)]
    return sampled, True


def truncation_marker(total: int, shown: int) -> str:
    """Human-readable line to prepend/log when a sample is truncated —
    same wording both callers use, so a reviewer sees one consistent
    phrase regardless of which one produced it."""
    return f"[step log truncated: {shown} of {total} steps shown]"
