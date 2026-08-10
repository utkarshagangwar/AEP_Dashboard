"""Unit tests for the derived-failure (spec-gap) selection rule.

Scope: the rule only — app.api.v1.ai_runs.is_spec_gap_candidate. The endpoint
around it is a query and a projection; the judgement it makes is here, and it
is the part that must not be wrong.

WHAT THE REPORT IS FOR. Most negative and edge tests are grounding="derived":
the document does not enumerate its own failure modes, so the expectation was
inferred from standard QA practice. A failing derived test therefore has two
possible explanations needing opposite responses — the product is wrong (raise
a defect), or the INFERENCE is wrong because the product deliberately behaves
differently and the document never said so (fix the document). This rule picks
out the cases where the second is likely.
"""
from __future__ import annotations

from app.api.v1.ai_runs import is_spec_gap_candidate
from app.models.ai_runs import AIRunStatus

FAILED = AIRunStatus.failed
PASSED = AIRunStatus.passed


def test_consistent_failure_is_a_candidate():
    assert is_spec_gap_candidate([FAILED, FAILED], min_runs=2)
    assert is_spec_gap_candidate([FAILED] * 7, min_runs=2)


def test_one_pass_anywhere_disqualifies_it():
    """A single pass proves the expectation and the product CAN agree, so the
    disagreement is not systematic — that is flakiness or data dependence,
    which is a different problem with a different owner."""
    assert not is_spec_gap_candidate([PASSED, FAILED, FAILED], min_runs=2)
    assert not is_spec_gap_candidate([FAILED, FAILED, PASSED], min_runs=2)
    assert not is_spec_gap_candidate([FAILED, PASSED, FAILED], min_runs=2)


def test_a_single_failure_is_not_enough_evidence():
    """One failure is an incident, not a pattern. Listing it would bury the
    real candidates in noise, and a report nobody reads reports nothing."""
    assert not is_spec_gap_candidate([FAILED], min_runs=2)


def test_min_runs_raises_the_bar_rather_than_changing_the_rule():
    assert not is_spec_gap_candidate([FAILED, FAILED], min_runs=3)
    assert is_spec_gap_candidate([FAILED, FAILED, FAILED], min_runs=3)


def test_a_skill_that_never_ran_is_never_a_candidate():
    """Absence of runs is absence of evidence. It must not read as a gap."""
    assert not is_spec_gap_candidate([], min_runs=2)
    assert not is_spec_gap_candidate([], min_runs=1)


def test_min_runs_of_one_still_requires_a_real_failure():
    assert is_spec_gap_candidate([FAILED], min_runs=1)
    assert not is_spec_gap_candidate([PASSED], min_runs=1)


def test_only_decided_statuses_count_as_failure():
    """The caller filters to passed/failed. If an undecided status ever
    reaches this function it must NOT be counted as a failure — treating
    "we don't know" as "it failed" would manufacture spec gaps out of
    infrastructure problems."""
    for undecided in (
        AIRunStatus.inconclusive,
        AIRunStatus.cancelled,
        AIRunStatus.pending,
        AIRunStatus.running,
    ):
        assert not is_spec_gap_candidate([FAILED, undecided], min_runs=2), undecided
