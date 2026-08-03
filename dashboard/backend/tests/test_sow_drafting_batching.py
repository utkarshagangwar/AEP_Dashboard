"""Generate SOW keeps every fact (regressions for the two silent-loss bugs).

Two independent truncations used to compound here, and between them most of
a large document's content never reached the generated SOW:

  * grouping sent the ENTIRE ledger in one call, so with several hundred
    facts the response was cut off, repaired into a short list, and the
    "Additional Items" net swept the unclaimed remainder into one section;
  * draft_section then did `facts[:60]` — every fact past the 60th vanished
    from the draft AND from the completeness net that was supposed to catch
    exactly that.

No network: llm_router is monkeypatched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import sow_drafting


class FakeResult(SimpleNamespace):
    def __init__(self, **kw):
        kw.setdefault("truncated", False)
        kw.setdefault("repaired", False)
        kw.setdefault("finish_reason", "stop")
        kw.setdefault("model_used", "fake-model")
        super().__init__(**kw)


class FakeFact:
    """Minimal stand-in for a SowRequirementsLedger row."""

    def __init__(self, i, fact_type="ui_element", heading_path=None):
        self.label = f"Control {i}"
        self.fact_type = fact_type
        self.element_type = "button" if fact_type == "ui_element" else None
        self.location = None
        self.behavior_notes = None
        self.source_heading_path = heading_path


def _facts(n, **kw):
    return [FakeFact(i, **kw) for i in range(n)]


# ── Regression for the 60-fact slice ─────────────────────────────────────────

def test_a_large_section_drafts_every_fact_not_just_the_first_60(monkeypatch):
    from app.services import llm_router

    calls = []

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        calls.append(prompt)
        # Echo back one control_spec per fact index present in the prompt.
        import re
        indices = [int(m) for m in re.findall(r"'index': (\d+)", prompt)]
        return FakeResult(parsed_json={"blocks": [
            {"type": "control_spec", "element_type": "button",
             "label": f"C{i}", "behavior": "does a thing", "fact_index": i}
            for i in indices
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    facts = _facts(250)
    blocks, _ = sow_drafting.draft_section("Big Section", facts)

    referenced = {
        b["fact_index"] for b in blocks
        if b.get("type") == "control_spec" and b.get("fact_index") is not None
    }
    assert referenced == set(range(250)), "facts past the old 60-fact cap were dropped"
    assert len(calls) == 6, "250 facts should draft in ceil(250/45) passes"
    # And nothing was quietly flagged as an unreferenced gap.
    assert not any(
        b.get("type") == "callout" and "auto-recovered" in b.get("text", "")
        for b in blocks
    )


def test_only_the_first_pass_emits_the_section_heading(monkeypatch):
    from app.services import llm_router

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        return FakeResult(parsed_json={"blocks": [
            {"type": "heading", "level": 2, "text": "Big Section"},
            {"type": "paragraph", "text": "Some prose."},
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    blocks, _ = sow_drafting.draft_section("Big Section", _facts(140))
    headings = [b for b in blocks if b.get("type") == "heading"]
    assert len(headings) == 1, "a continuation pass duplicated the section heading"


def test_unreferenced_facts_are_still_flagged_across_all_passes(monkeypatch):
    """The completeness net must see the FULL fact list — under the old cap
    it only ever saw the surviving 60, so it could not flag the rest."""
    from app.services import llm_router

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        return FakeResult(parsed_json={"blocks": [
            {"type": "paragraph", "text": "Prose that mentions nothing specific."}
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    blocks, _ = sow_drafting.draft_section("Big Section", _facts(120))
    callouts = [
        b for b in blocks
        if b.get("type") == "callout" and "auto-recovered" in b.get("text", "")
    ]
    assert len(callouts) == 1
    assert "Control 119" in callouts[0]["text"], "late facts were not flagged"


def test_a_partial_drafting_failure_is_visible_in_the_document(monkeypatch):
    from app.services import llm_router

    calls = {"n": 0}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise llm_router.LLMRouterError("provider down")
        return FakeResult(parsed_json={"blocks": [{"type": "paragraph", "text": "x"}]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    blocks, _ = sow_drafting.draft_section("Big Section", _facts(120))
    assert any(
        b.get("type") == "callout" and "drafting pass(es) for this section failed"
        in b.get("text", "")
        for b in blocks
    ), "a partially drafted section must say so, not read as complete"


def test_every_pass_failing_still_raises(monkeypatch):
    from app.services import llm_router
    from app.services.design_ingest import IngestError

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        raise llm_router.LLMRouterError("provider down")

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    with pytest.raises(IngestError):
        sow_drafting.draft_section("Big Section", _facts(10))


# ── Regression for single-call grouping ──────────────────────────────────────

def test_grouping_batches_and_claims_every_index(monkeypatch):
    from app.services import llm_router

    calls = []

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        calls.append(prompt)
        if "Consolidate these headings" in prompt:
            return FakeResult(parsed_json={"mapping": []})
        import re
        indices = [int(m) for m in re.findall(r"'index': (\d+)", prompt)]
        half = len(indices) // 2
        return FakeResult(parsed_json={"sections": [
            {"heading": "Login", "fact_indices": indices[:half]},
            {"heading": "Dashboard", "fact_indices": indices[half:]},
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    facts = _facts(500)
    groups, _ = sow_drafting.group_ledger_into_sections(facts)

    claimed = [i for g in groups for i in g["fact_indices"]]
    assert sorted(claimed) == list(range(500)), "an index was lost or duplicated"
    assert len(claimed) == len(set(claimed))

    additional = [g for g in groups if g["heading"] == "Additional Items"]
    swept = sum(len(g["fact_indices"]) for g in additional)
    assert swept < 500 * 0.1, "the catch-all section absorbed most of the ledger"

    grouping_calls = [c for c in calls if "Consolidate these headings" not in c]
    assert len(grouping_calls) == 7, "500 facts should group in ceil(500/80) batches"


def test_near_duplicate_headings_from_different_batches_are_merged(monkeypatch):
    from app.services import llm_router

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        if "Consolidate these headings" in prompt:
            return FakeResult(parsed_json={"mapping": [
                {"from": "Skills Table Filters", "to": "Skills Table — Filters"},
                {"from": "Skills Table — Filters", "to": "Skills Table — Filters"},
            ]})
        import re
        indices = [int(m) for m in re.findall(r"'index': (\d+)", prompt)]
        heading = "Skills Table Filters" if indices[0] == 0 else "Skills Table — Filters"
        return FakeResult(parsed_json={"sections": [
            {"heading": heading, "fact_indices": indices}
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    groups, _ = sow_drafting.group_ledger_into_sections(_facts(160))
    assert len(groups) == 1, "the same section from two batches was not merged"
    assert groups[0]["fact_indices"] == list(range(160))


def test_a_failed_grouping_batch_does_not_lose_its_facts(monkeypatch):
    from app.services import llm_router

    calls = {"n": 0}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        if "Consolidate these headings" in prompt:
            return FakeResult(parsed_json={"mapping": []})
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm_router.LLMRouterError("provider down")
        import re
        indices = [int(m) for m in re.findall(r"'index': (\d+)", prompt)]
        return FakeResult(parsed_json={"sections": [
            {"heading": "Dashboard", "fact_indices": indices}
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    groups, _ = sow_drafting.group_ledger_into_sections(_facts(160))
    claimed = sorted(i for g in groups for i in g["fact_indices"])
    assert claimed == list(range(160)), "the failed batch's facts were dropped"


def test_all_grouping_batches_failing_raises(monkeypatch):
    from app.services import llm_router
    from app.services.design_ingest import IngestError

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        raise llm_router.LLMRouterError("provider down")

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    with pytest.raises(IngestError):
        sow_drafting.group_ledger_into_sections(_facts(10))
