"""A new source updates only the sections it actually touches.

Attaching a transcript to an already-generated SOW previously left two bad
options: regenerate everything (losing hand edits, re-paying for every
section) or guess which sections to rewrite from a checkbox list. This
module computes the answer.

Advisory by design — it stamps assignments and retires restated facts, but
never redrafts. The caller (the Rewrite dialog) is what spends tokens.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import sow_impact


class FakeResult(SimpleNamespace):
    def __init__(self, **kw):
        kw.setdefault("truncated", False)
        kw.setdefault("repaired", False)
        kw.setdefault("finish_reason", "stop")
        kw.setdefault("model_used", "fake-model")
        super().__init__(**kw)


class FakeFact:
    def __init__(self, label, *, fact_type="ui_element", element_type="button",
                 heading_path=None, source_artifact_id="src-new", id=None):
        self.id = id or f"fact-{label}"
        self.label = label
        self.fact_type = fact_type
        self.element_type = element_type
        self.location = None
        self.behavior_notes = None
        self.source_heading_path = heading_path
        self.source_artifact_id = source_artifact_id
        self.superseded = False
        self.assigned_section_key = None


SECTIONS = {
    "user-login": "User Login",
    "demo-list-page": "Demo List Page",
    "workspace-listing": "Workspace Listing",
}


# ── Assignment by the document's own outline (no LLM) ────────────────────────

def test_facts_are_matched_to_sections_by_source_heading(monkeypatch):
    from app.services import llm_router

    def explode(*a, **kw):
        raise AssertionError("the outline path must not call the LLM")

    monkeypatch.setattr(llm_router, "complete_json_complete", explode)

    facts = [
        FakeFact("Edit", heading_path=["SOW", "Demo List Page"]),
        FakeFact("Save", heading_path=["SOW", "Demo List Page"]),
        FakeFact("Sign in", heading_path=["SOW", "User Login"]),
    ]
    assignments = sow_impact.assign_new_facts_to_sections(facts, SECTIONS)

    assert assignments == {0: "demo-list-page", 1: "demo-list-page", 2: "user-login"}


def test_the_llm_path_is_used_when_facts_carry_no_outline(monkeypatch):
    from app.services import llm_router

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        assert "Existing sections:" in prompt
        return FakeResult(parsed_json={"assignments": [
            {"index": 0, "section_key": "user-login"},
            {"index": 1, "section_key": "workspace-listing"},
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    facts = [FakeFact("Sign in"), FakeFact("Delete workspace")]
    assignments = sow_impact.assign_new_facts_to_sections(facts, SECTIONS)

    assert assignments == {0: "user-login", 1: "workspace-listing"}


def test_an_unknown_section_key_is_left_unassigned(monkeypatch):
    """Better to surface a fact as homeless than to file it somewhere wrong."""
    from app.services import llm_router

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        return FakeResult(parsed_json={"assignments": [
            {"index": 0, "section_key": "__new__"},
            {"index": 1, "section_key": "a-section-that-does-not-exist"},
        ]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    assignments = sow_impact.assign_new_facts_to_sections(
        [FakeFact("A"), FakeFact("B")], SECTIONS
    )
    assert assignments == {}


def test_a_partially_matching_outline_falls_back_to_the_llm(monkeypatch):
    """Below the coverage threshold the outline isn't trustworthy on its own."""
    from app.services import llm_router

    called = {"n": 0}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        called["n"] += 1
        return FakeResult(parsed_json={"assignments": []})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    facts = [
        FakeFact("A", heading_path=["SOW", "Demo List Page"]),
        FakeFact("B"),
        FakeFact("C"),
        FakeFact("D"),
    ]
    sow_impact.assign_new_facts_to_sections(facts, SECTIONS)
    assert called["n"] == 1


def test_no_sections_means_no_work(monkeypatch):
    assert sow_impact.assign_new_facts_to_sections([FakeFact("A")], {}) == {}
    assert sow_impact.assign_new_facts_to_sections([], SECTIONS) == {}


# ── Supersession — the missing write half of the ledger's audit trail ────────

class FakeLedgerQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **kw):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **kw):
        return FakeLedgerQuery(self._rows)


def test_an_older_fact_restated_by_a_new_source_is_retired():
    old = FakeFact("Bulk delete", source_artifact_id="src-old", id="old-1")
    unrelated = FakeFact("Export CSV", source_artifact_id="src-old", id="old-2")
    new = FakeFact("Bulk delete", source_artifact_id="src-new", id="new-1")

    retired = sow_impact.mark_superseded(
        FakeSession([old, unrelated, new]), "doc-1", [new]
    )

    assert retired == 1
    assert old.superseded is True
    assert unrelated.superseded is False
    assert new.superseded is False, "the new fact must never retire itself"


def test_facts_from_the_same_source_are_not_retired():
    """Dedup already merged those; retiring a fact against its own source
    would erase that source's own contribution."""
    sibling = FakeFact("Bulk delete", source_artifact_id="src-new", id="sib-1")
    new = FakeFact("Bulk delete", source_artifact_id="src-new", id="new-1")

    retired = sow_impact.mark_superseded(FakeSession([sibling, new]), "doc-1", [new])

    assert retired == 0
    assert sibling.superseded is False


def test_a_different_control_type_is_not_treated_as_the_same_fact():
    old = FakeFact("Status", element_type="dropdown",
                   source_artifact_id="src-old", id="old-1")
    new = FakeFact("Status", element_type="filter",
                   source_artifact_id="src-new", id="new-1")

    retired = sow_impact.mark_superseded(FakeSession([old, new]), "doc-1", [new])

    assert retired == 0
    assert old.superseded is False


def test_nothing_new_means_nothing_retired():
    assert sow_impact.mark_superseded(FakeSession([]), "doc-1", []) == 0
