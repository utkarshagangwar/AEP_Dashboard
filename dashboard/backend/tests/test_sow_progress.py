"""Unit tests for live extraction progress.

Scope: which events the extraction engine emits, and that reporting can never
break extraction. No database — the emitter is exercised through the callback
contract that app/services/sow_progress.reporter() produces.

WHAT THESE PIN DOWN. The panel's whole value is that it reports what actually
ran. A fixed list of phases would be easier and would also lie: it would claim
"identifying feature sections" on a run with zoning disabled and stay silent on
gap repair. So the tests care less about exact wording than about which events
appear, and — more importantly — which ones DON'T.
"""
from __future__ import annotations

import pytest

from app.services import sow_progress, tdd_extraction as tdd


class _Reply:
    def __init__(self, parsed_json):
        self.parsed_json = parsed_json
        self.model_used = "stub-model"


@pytest.fixture
def recorded():
    """Collects (stage, status, description) tuples from the callback."""
    events: list[tuple[str, str, str]] = []

    def _on_progress(stage, status, description, detail=None):
        events.append((stage, status, description))

    _on_progress.events = events  # type: ignore[attr-defined]
    return _on_progress


def _stages(on_progress):
    return [e[0] for e in on_progress.events]


def _status_of(on_progress, stage):
    return next((e[1] for e in on_progress.events if e[0] == stage), None)


def _stub_all(monkeypatch, behaviours=None, repairs=None):
    """Stub both provider entry points the extraction path uses."""
    import sys

    class _Router:
        @staticmethod
        def complete_json_complete(prompt, *, system, max_tokens, **kwargs):
            return _Reply({"mapping": []})

    monkeypatch.setattr("app.services.llm_router", _Router, raising=False)
    monkeypatch.setitem(sys.modules, "app.services.llm_router", _Router)

    from app.services import design_ingest

    def _brain(prompt, *, system, max_tokens):
        if "missing test cases" in prompt:
            return _Reply({"repairs": repairs or []})
        return _Reply({"behaviours": behaviours or []})

    monkeypatch.setattr(design_ingest, "_complete_via_brain", _brain)


_BEHAVIOUR = {
    "behaviour_key": "create-a-job",
    "category": "crud",
    "checkpoints": [
        {
            # "type" is required by design_ingest.validate_checkpoint — a
            # checkpoint without it is dropped as schema-invalid, which is
            # what a real model reply always carries.
            "type": "functional",
            "test_type": "positive",
            "title": "Create a job",
            "objective": "Create a job from the jobs list page",
            "instructions": ["Open the jobs list", "Click Create Job", "Confirm it appears"],
            "grounding": "stated",
        }
    ],
}

_TEXT = (
    "# Job Creation\n"
    "A recruiter can create a job from the jobs list page. The system must "
    "reject a job with no title and show a validation error.\n"
)


def test_reports_what_actually_ran(monkeypatch, recorded):
    monkeypatch.setenv("TDD_ZONING", "0")  # no LLM zoner in a unit test
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    tdd.extract(_TEXT, on_progress=recorded)

    stages = _stages(recorded)
    assert "segment" in stages
    assert "zoning" in stages
    assert "extract" in stages


def test_a_disabled_stage_reports_skipped_not_done(monkeypatch, recorded):
    """The precise dishonesty a fixed step list produces: reporting a stage
    that never ran as completed work."""
    monkeypatch.setenv("TDD_ZONING", "0")
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    tdd.extract(_TEXT, on_progress=recorded)

    assert _status_of(recorded, "zoning") == sow_progress.SKIPPED


def test_repair_reports_skipped_when_there_was_nothing_to_repair(monkeypatch, recorded):
    """"Nothing needed fixing" and "the repair pass never ran" must not both
    render as a green tick."""
    monkeypatch.setenv("TDD_ZONING", "0")
    # crud requires negative + edge, so the single positive above leaves a gap.
    # Give it every variant instead, so there is genuinely nothing to repair.
    complete = {
        "behaviour_key": "create-a-job",
        "category": "crud",
        "checkpoints": [
            {
                "type": "functional",
                "test_type": t,
                "title": f"{t} case",
                "objective": f"{t} objective for creating a job",
                "instructions": ["Open the jobs list", "Act", "Confirm the outcome"],
                "grounding": "stated",
            }
            for t in ("positive", "negative", "edge")
        ],
    }
    _stub_all(monkeypatch, behaviours=[complete])

    tdd.extract(_TEXT, on_progress=recorded)

    assert _status_of(recorded, "repair") == sow_progress.SKIPPED


def test_no_event_is_emitted_for_a_stage_that_did_nothing(monkeypatch, recorded):
    """Dedupe collapsed nothing, so there is no dedupe row. A panel showing
    'Collapsed 0 repeated tests' is noise pretending to be progress."""
    monkeypatch.setenv("TDD_ZONING", "0")
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    tdd.extract(_TEXT, on_progress=recorded)

    assert "dedupe" not in _stages(recorded)


def test_a_discarded_zoning_verdict_is_reported_as_such(monkeypatch, recorded):
    """A part that is ENTIRELY commercial terms trips the 85% safety valve:
    excluding everything is likelier to be a classifier misread than a
    document with no requirements, so the verdict is thrown away and the whole
    part is extracted anyway (spec §4.3 step 4).

    The valve returns excluded=[] — indistinguishable from "nothing needed
    excluding" — so a panel deriving its message from the return value would
    report a classifier malfunction as a clean pass. That is exactly why
    classify_zones emits its own event instead of leaving it to the caller.
    """
    monkeypatch.setenv("TDD_ZONING", "1")
    _stub_all(monkeypatch)

    tdd.extract(
        "# 7. Commercial Terms\n" + "The engagement is billed monthly in arrears. " * 6,
        on_progress=recorded,
    )

    zoning = next(e for e in recorded.events if e[0] == "zoning")
    assert "ignoring it" in zoning[2], zoning[2]
    assert "all describe product behaviour" not in zoning[2]


def test_zoning_reports_what_it_set_aside(monkeypatch, recorded):
    """A mixed document: the commercial section goes, the feature section
    stays, and the panel names the kind that was excluded."""
    monkeypatch.setenv("TDD_ZONING", "1")
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    tdd.extract(
        "# 7. Commercial Terms\n"
        + "The engagement is billed monthly in arrears. " * 4
        + "\n\n# Job Creation\n"
        + "A recruiter can create a job from the jobs list page. " * 8,
        on_progress=recorded,
    )

    zoning = next(e for e in recorded.events if e[0] == "zoning")
    assert zoning[1] == sow_progress.DONE
    assert "commercial" in zoning[2]


def test_extraction_runs_identically_with_no_callback(monkeypatch):
    """Progress is optional: the engine has callers with no artifact at all."""
    monkeypatch.setenv("TDD_ZONING", "0")
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    with_cb = tdd.extract(_TEXT, on_progress=lambda *a, **k: None)
    without = tdd.extract(_TEXT)

    # Non-zero on both sides, or this asserts nothing: 0 == 0 would pass with
    # the fixture silently rejected as schema-invalid.
    assert len(with_cb.checkpoints) >= 1
    assert len(with_cb.checkpoints) == len(without.checkpoints)


def test_a_broken_callback_cannot_break_extraction(monkeypatch):
    """Reporting must never be able to fail the work it reports on."""
    monkeypatch.setenv("TDD_ZONING", "0")
    _stub_all(monkeypatch, behaviours=[_BEHAVIOUR])

    def _explode(*_a, **_k):
        raise RuntimeError("progress backend down")

    result = tdd.extract(_TEXT, on_progress=_explode)
    assert len(result.checkpoints) >= 1


def test_report_helper_tolerates_no_callback():
    sow_progress.report(None, "stage", sow_progress.DONE, "nothing should happen")
