"""A regenerated SOW mirrors the imported document's own structure.

An imported SOW already HAS an organisation, chosen by whoever wrote it.
Asking a model to invent a fresh one discards that — the document comes back
reordered and renamed, which reads as "it lost my content" even when every
fact survived. When facts carry the heading they physically came from,
grouping uses it directly: deterministic, in the source's order, and with no
LLM call at all.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import sow_drafting, sow_ledger
from app.services.doc_chunking import Chunk


class FakeFact:
    def __init__(self, i, heading_path=None):
        self.label = f"Control {i}"
        self.fact_type = "ui_element"
        self.element_type = "button"
        self.location = None
        self.behavior_notes = None
        self.source_heading_path = heading_path


_OUTLINE = [
    ["SOW", "1. Feature Overview"],
    ["SOW", "21. Demo List Page"],
    ["SOW", "39. Feature Controls"],
]


def _facts_from_outline(per_section=3):
    facts = []
    for path in _OUTLINE:
        for i in range(per_section):
            facts.append(FakeFact(f"{path[-1]}-{i}", heading_path=list(path)))
    return facts


# ── Deterministic grouping ───────────────────────────────────────────────────

def test_grouping_uses_the_source_outline_and_calls_no_llm(monkeypatch):
    from app.services import llm_router

    def explode(*a, **kw):
        raise AssertionError("an import-sourced document must not need the LLM")

    monkeypatch.setattr(llm_router, "complete_json_complete", explode)

    groups, model = sow_drafting.group_ledger_into_sections(_facts_from_outline())

    assert [g["heading"] for g in groups] == [
        "1. Feature Overview", "21. Demo List Page", "39. Feature Controls",
    ], "sections must appear in the source document's own order"
    assert model == "source-outline (deterministic)"


def test_every_fact_is_claimed_exactly_once(monkeypatch):
    from app.services import llm_router
    monkeypatch.setattr(llm_router, "complete_json_complete",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))

    facts = _facts_from_outline(per_section=5)
    groups, _ = sow_drafting.group_ledger_into_sections(facts)

    claimed = [i for g in groups for i in g["fact_indices"]]
    assert sorted(claimed) == list(range(len(facts)))
    assert len(claimed) == len(set(claimed))


def test_section_keys_are_stable_slugs_of_the_source_headings(monkeypatch):
    from app.services import llm_router
    monkeypatch.setattr(llm_router, "complete_json_complete",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))

    groups, _ = sow_drafting.group_ledger_into_sections(_facts_from_outline())
    keys = [g["section_key"] for g in groups]

    assert keys == ["1-feature-overview", "21-demo-list-page", "39-feature-controls"]
    assert len(set(keys)) == len(keys)


def test_a_few_unstructured_facts_are_swept_into_additional_items(monkeypatch):
    from app.services import llm_router
    monkeypatch.setattr(llm_router, "complete_json_complete",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))

    facts = _facts_from_outline(per_section=5) + [FakeFact("stray")]
    groups, _ = sow_drafting.group_ledger_into_sections(facts)

    additional = [g for g in groups if g["heading"] == "Additional Items"]
    assert len(additional) == 1
    assert additional[0]["fact_indices"] == [len(facts) - 1]


def test_a_transcript_still_uses_the_llm_grouping_pass(monkeypatch):
    """Transcripts, recordings and design images genuinely have no structure
    to mirror — they must keep the model-driven path."""
    from app.services import llm_router

    calls = {"n": 0}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        calls["n"] += 1
        if "Consolidate these headings" in prompt:
            return SimpleNamespace(parsed_json={"mapping": []}, model_used="m",
                                   truncated=False, repaired=False, finish_reason="stop")
        import re
        indices = [int(m) for m in re.findall(r"'index': (\d+)", prompt)]
        return SimpleNamespace(
            parsed_json={"sections": [{"heading": "Login", "fact_indices": indices}]},
            model_used="m", truncated=False, repaired=False, finish_reason="stop",
        )

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    groups, model = sow_drafting.group_ledger_into_sections(
        [FakeFact(i) for i in range(6)]
    )
    assert calls["n"] >= 1
    assert model != "source-outline (deterministic)"
    assert groups[0]["heading"] == "Login"


# ── The outline is captured at extraction time ───────────────────────────────

def _chunk(index, heading_path, locator=None):
    return Chunk(
        index=index, total=3, text="body",
        heading_path=list(heading_path), locator=locator,
        strategy="heading_tree", context_header="",
    )


def test_outline_records_each_heading_once_in_document_order():
    chunks = [
        _chunk(1, ["SOW", "1. Feature Overview"], "p.1"),
        _chunk(2, ["SOW", "1. Feature Overview"], "p.2"),  # same section, 2 chunks
        _chunk(3, ["SOW", "21. Demo List Page"], "p.9"),
    ]
    outline = sow_ledger._outline_from_chunks(chunks)

    assert [o["heading"] for o in outline] == ["1. Feature Overview", "21. Demo List Page"]
    assert outline[0]["heading_path"] == ["SOW", "1. Feature Overview"]
    assert outline[1]["locator"] == "p.9"


def test_a_document_with_no_headings_yields_an_empty_outline():
    assert sow_ledger._outline_from_chunks([_chunk(1, [])]) == []
