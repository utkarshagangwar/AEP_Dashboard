"""Ledger extraction recovers from a response that didn't fit.

Three layers protect against output truncation, and this file covers the
last one — the structural remedy. When a chunk's answer is cut off even
after llm_router escalated the token budget, the chunk is halved and
re-asked. The halves keep the parent's document framing, and the
unrecoverable case leaves a visible marker fact rather than a quietly
short list.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import sow_ledger
from app.services.doc_chunking import Chunk


class FakeResult(SimpleNamespace):
    def __init__(self, **kw):
        kw.setdefault("truncated", False)
        kw.setdefault("repaired", False)
        kw.setdefault("finish_reason", "stop")
        kw.setdefault("model_used", "fake-model")
        super().__init__(**kw)


def _chunk(text: str, *, index=1, total=1, heading=("Docs", "3. Demo List")):
    return Chunk(
        index=index,
        total=total,
        text=text,
        heading_path=list(heading),
        locator="p.7",
        strategy="heading_tree",
        context_header="<document_context>\nDocument: Acme\n</document_context>",
    )


def _fact(label: str) -> dict:
    return {
        "fact_type": "ui_element", "element_type": "button", "label": label,
        "location": None, "behavior_notes": None, "source_ref": None,
    }


# A body long enough that _subdivide can actually split it, with paragraph
# boundaries so the split lands on one.
_BODY = "\n\n".join(f"Paragraph {i} describing a control in some detail." for i in range(40))


@pytest.fixture
def scripted_llm(monkeypatch):
    """complete_json_complete driven by a per-call script keyed on how much
    text the prompt carries: the first (whole) call truncates, and the
    smaller follow-up calls succeed."""
    from app.services import llm_router

    state = {"calls": [], "truncate_if_longer_than": 10**9, "always_truncate": False}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        state["calls"].append(prompt)
        n = len(state["calls"])
        too_long = len(prompt) > state["truncate_if_longer_than"]
        if state["always_truncate"] or too_long:
            return FakeResult(parsed_json={"facts": [_fact(f"partial {n}")]}, truncated=True)
        return FakeResult(parsed_json={"facts": [_fact(f"Control {n}")]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)
    monkeypatch.setattr(sow_ledger, "_CHUNK_BACKOFF_SECONDS", (0, 0))
    return state


# ── Recovery by splitting ────────────────────────────────────────────────────

def test_truncated_chunk_is_split_and_both_halves_are_kept(scripted_llm):
    """The regression that matters: a chunk whose answer didn't fit must not
    silently return the short answer."""
    scripted_llm["truncate_if_longer_than"] = len(_BODY) // 2 + 200

    facts, models = sow_ledger._extract_with_split(
        _chunk(_BODY),
        sow_ledger.extract_ledger_from_sow_document,
        source_kind="existing SOW document",
    )

    labels = {f["label"] for f in facts}
    assert len(scripted_llm["calls"]) == 3, "one whole call, then one per half"
    assert len(labels) == 2, "both halves' facts must survive"
    assert not any(label.startswith("partial") for label in labels)
    assert models == ["fake-model"]


def test_halves_keep_the_parent_document_framing(scripted_llm):
    """Splitting must not cost the model the section context that
    SOW_CHUNKING_PLAN Phase 3 exists to provide."""
    parent = _chunk(_BODY)
    halves = sow_ledger._subdivide(parent)

    assert len(halves) == 2
    for half in halves:
        assert half.heading_path == parent.heading_path
        assert half.context_header == parent.context_header
        assert half.locator == parent.locator
    # No content is dropped by the split (whitespace-insensitive).
    joined = (halves[0].text + halves[1].text).replace("\n", "").replace(" ", "")
    assert joined == parent.text.replace("\n", "").replace(" ", "")


def test_overflowing_chunk_also_triggers_a_split(scripted_llm, monkeypatch):
    """Too many facts is the other 'the answer didn't fit' signal, and it
    used to fail the entire import instead of the one chunk."""
    from app.services import llm_router

    calls = {"n": 0}

    def fake(prompt, *, system=None, max_tokens=0, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            over = sow_ledger._MAX_FACTS_PER_CALL + 5
            return FakeResult(parsed_json={"facts": [_fact(f"B{i}") for i in range(over)]})
        return FakeResult(parsed_json={"facts": [_fact(f"Control {calls['n']}")]})

    monkeypatch.setattr(llm_router, "complete_json_complete", fake)

    facts, _ = sow_ledger._extract_with_split(
        _chunk(_BODY),
        sow_ledger.extract_ledger_from_sow_document,
        source_kind="existing SOW document",
    )
    assert calls["n"] == 3
    assert len(facts) == 2


# ── The unrecoverable case is visible, not silent ────────────────────────────

def test_recursion_stops_and_records_the_gap(scripted_llm):
    scripted_llm["always_truncate"] = True

    facts, _ = sow_ledger._extract_with_split(
        _chunk(_BODY),
        sow_ledger.extract_ledger_from_sow_document,
        source_kind="existing SOW document",
    )

    # 1 + 2 + 4 = 7 calls at _MAX_SPLIT_DEPTH=2, then it gives up.
    assert len(scripted_llm["calls"]) == 7
    assert facts, "an unrecoverable chunk must still leave a trace"
    assert all(f["fact_type"] == "open_question" for f in facts)
    assert all("Extraction incomplete" in f["label"] for f in facts)
    assert any("3. Demo List" in f["label"] for f in facts)


def test_a_chunk_too_small_to_split_records_the_gap_immediately(scripted_llm):
    scripted_llm["always_truncate"] = True

    facts, _ = sow_ledger._extract_with_split(
        _chunk("Tiny."),
        sow_ledger.extract_ledger_from_sow_document,
        source_kind="existing SOW document",
    )
    assert len(scripted_llm["calls"]) == 1
    assert len(facts) == 1
    assert facts[0]["fact_type"] == "open_question"


def test_subdivide_refuses_to_split_something_tiny():
    assert sow_ledger._subdivide(_chunk("Short text.")) == []


# ── Imported-document structure is stamped on every fact ─────────────────────

def test_facts_carry_the_source_heading_path(scripted_llm):
    facts, _ = sow_ledger._extract_with_split(
        _chunk("Some ordinary content about a button."),
        sow_ledger.extract_ledger_from_sow_document,
        source_kind="existing SOW document",
    )
    assert facts
    for fact in facts:
        assert fact["source_heading_path"] == ["Docs", "3. Demo List"]


def test_transcript_facts_carry_no_heading_path(scripted_llm):
    """Only imported documents have an outline to mirror; a transcript must
    not be given a fabricated one."""
    facts, _ = sow_ledger._extract_with_split(
        _chunk("Alice: we need a bulk delete button.", heading=()),
        sow_ledger.extract_ledger_from_text,
        source_kind="transcript",
    )
    assert facts
    assert all("source_heading_path" not in f for f in facts)
