"""Phase 3 + 4 — prompt assembly and failure semantics (T-P-*, T-F-*).

SOW_CHUNKING_PLAN.md §3. These tests never call an LLM: llm_router.complete
is monkeypatched, so the assertions are about what we SEND and how we handle
what comes back. The one test that needs a live model (T-P-004, whether the
model actually obeys the preceding-context fence) belongs in golden_tests/.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services import sow_ledger
from app.services.doc_blocks import IngestError, extract_blocks
from app.services.doc_chunking import chunk_document


class FakeResult(SimpleNamespace):
    """Stand-in for llm_router.RouterResult.

    `truncated` defaults False so every fixture below describes a response
    that fit — a test that wants the truncation path says so explicitly.
    """

    def __init__(self, **kw):
        kw.setdefault("truncated", False)
        kw.setdefault("repaired", False)
        kw.setdefault("finish_reason", "stop")
        super().__init__(**kw)


@pytest.fixture
def capture_llm(monkeypatch):
    """Replace llm_router.complete with a recorder. Returns the call log."""
    from app.services import llm_router

    calls: list[dict] = []

    def fake_complete(prompt, *, system=None, expect_json=False, max_tokens=0, **kw):
        calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return FakeResult(
            parsed_json={"facts": [
                {"fact_type": "ui_element", "element_type": "button",
                 "label": "Bulk delete", "location": None,
                 "behavior_notes": None, "source_ref": "§2.1"},
            ]},
            model_used="fake-model",
        )

    monkeypatch.setattr(llm_router, "complete", fake_complete)
    return calls


@pytest.fixture
def failing_llm(monkeypatch):
    """llm_router.complete with injectable failures.

    Failures are targeted by PROMPT CONTENT ("fail every call whose prompt
    mentions part 2 of N") rather than by call index, because the retry loop
    makes the mapping from call index to chunk depend on how many retries
    already happened -- an index-based fixture would silently test the wrong
    chunk as soon as the retry policy changed.
    """
    from app.services import llm_router

    state = {"calls": 0, "fail_if_prompt_contains": None, "transient_failures": 0}

    def fake_complete(prompt, *, system=None, **kw):
        state["calls"] += 1
        if state["transient_failures"] > 0:
            state["transient_failures"] -= 1
            raise llm_router.LLMRouterError("transient provider failure")
        needle = state["fail_if_prompt_contains"]
        if needle and needle in prompt:
            raise llm_router.LLMRouterError("permanent provider failure")
        return FakeResult(
            parsed_json={"facts": [{
                "fact_type": "ui_element", "element_type": "button",
                "label": f"Control {state['calls']}", "location": None,
                "behavior_notes": None, "source_ref": None,
            }]},
            model_used="fake-model",
        )

    monkeypatch.setattr(llm_router, "complete", fake_complete)
    monkeypatch.setattr(sow_ledger, "_CHUNK_BACKOFF_SECONDS", (0, 0))
    return state


# ── T-P-001 / T-P-002 / T-P-003 — context header ─────────────────────────────

def _multi_chunk(fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    return chunk_document(
        blocks, file_name="structured.docx", document_title="Acme SOW", max_chars=250
    )


def test_tp001_header_contains_title_section_part_and_range(fixture_path):
    chunks = _multi_chunk(fixture_path)
    assert len(chunks) > 1

    header = chunks[1].context_header
    assert "Document: Acme SOW" in header
    assert "Section path: " in header
    assert re.search(r"Part 2 of \d+", header)
    assert re.search(r"characters [\d,]+-[\d,]+ of [\d,]+", header)


def test_tp002_preceding_context_present_from_chunk_two_onward(fixture_path):
    chunks = _multi_chunk(fixture_path)
    assert "<preceding_context" not in chunks[0].context_header
    for chunk in chunks[1:]:
        assert "<preceding_context" in chunk.context_header
        assert "DO NOT extract facts from this block" in chunk.context_header


def test_tp002_preceding_context_is_tail_of_previous_chunk(fixture_path):
    from app.services.doc_chunking import CONTEXT_TAIL_CHARS

    chunks = _multi_chunk(fixture_path)
    tail = chunks[0].text[-CONTEXT_TAIL_CHARS:]
    assert tail in chunks[1].context_header


def test_tp003_overlap_never_appears_in_extractable_text(fixture_path):
    """The trap this design avoids: chunk overlap that is also extractable
    turns boundary loss into duplicate facts."""
    chunks = _multi_chunk(fixture_path)
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.text not in current.text
        assert current.text not in previous.text


def test_tp003_content_is_fenced_in_the_prompt(fixture_path):
    chunk = _multi_chunk(fixture_path)[1]
    prompt = chunk.prompt_text()
    assert prompt.index("<preceding_context") < prompt.index("<content>")
    assert prompt.endswith("</content>")


# ── T-P-004 (unit half) — the instruction is actually sent ───────────────────

def test_tp004_excerpt_rule_sent_only_for_multi_chunk(capture_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")

    sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", document_title="Acme SOW", max_chars=250
    )
    assert len(capture_llm) > 1
    for call in capture_llm:
        assert "never extract a fact whose evidence appears solely" in call["system"]

    capture_llm.clear()
    sow_ledger.extract_ledger_from_sow_document_full(
        [{"kind": "paragraph", "text": "One small paragraph."}],
        file_name="small.md",
        max_chars=250,
    )
    assert len(capture_llm) == 1
    assert "never extract a fact whose evidence appears solely" not in capture_llm[0]["system"]


def test_tp005_section_path_offered_as_location_default(capture_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )
    assert any(
        "Use the 'Section path' in <document_context> as the default 'location'"
        in call["system"]
        for call in capture_llm
    )


# ── T-P-006 / T-P-007 — callers use the new chunker ──────────────────────────

def test_tp006_sow_document_path_sends_section_paths(capture_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", document_title="Acme SOW", max_chars=250
    )
    joined = "\n".join(c["prompt"] for c in capture_llm)
    assert "Section path: " in joined
    assert "2.1 Candidate List" in joined


def test_tp007_transcript_path_uses_speaker_turns(capture_llm, fixture_path):
    text = open(fixture_path("meeting.txt"), encoding="utf-8").read()
    sow_ledger.extract_ledger_from_transcript(
        text, document_title="Kickoff call", max_chars=1_500
    )

    assert len(capture_llm) > 1
    for call in capture_llm:
        content = call["prompt"].split("<content>")[-1].lstrip("\n")
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s\w+:", content)


def test_tp009_legacy_chunk_text_shim_still_returns_legacy_output(fixture_path):
    from app.services.design_ingest import chunk_text

    text = open(fixture_path("flat.txt"), encoding="utf-8").read()
    parts = chunk_text(text, max_chars=5_000)
    assert len(parts) > 1
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


# ── T-F-001..003 — partial extraction is SAVED, but never silently ───────────
#
# These previously asserted all-or-nothing: one failed chunk discarded every
# other chunk's facts. That reasoning ("an incomplete ledger must not silently
# become the SOW baseline") is still enforced — but by a distinct
# done_with_errors status and a named failure list, not by throwing away
# seventeen successful extractions because the eighteenth blipped.

def test_tf001_partial_extraction_keeps_good_parts_and_names_the_failure(
    failing_llm, fixture_path
):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    failing_llm["fail_if_prompt_contains"] = "Part 2 of"

    facts, _, failures, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )

    assert facts, "facts from the parts that succeeded must be kept"
    assert len(failures) == 1
    assert "part 2 of" in failures[0]


def test_tf002_transient_failure_is_retried_then_succeeds(failing_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    failing_llm["transient_failures"] = 2  # first two calls fail, then recover

    facts, model_used, failures, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )
    assert facts
    assert model_used == "fake-model"
    assert failures == []


def test_tf003_total_failure_still_raises(failing_llm, fixture_path):
    """Partial is saved; NOTHING is not. A run where every part failed has
    no facts worth keeping and no partial state worth explaining."""
    from app.services import llm_router

    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")

    def always_fail(prompt, *, system=None, **kw):
        raise llm_router.LLMRouterError("provider down")

    import pytest as _pytest  # local alias keeps the monkeypatch scope obvious

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm_router, "complete", always_fail)
        with pytest.raises(IngestError) as excinfo:
            sow_ledger.extract_ledger_from_sow_document_full(
                blocks, file_name="structured.docx", max_chars=250
            )
    assert "No facts were saved" in str(excinfo.value)


def test_tf001_failure_text_is_user_safe(failing_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    failing_llm["fail_if_prompt_contains"] = "Part 1 of"

    _, _, failures, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )
    assert failures
    assert "Traceback" not in "; ".join(failures)


def test_partial_source_is_marked_done_with_errors_not_done():
    """The invariant that makes saving partial results acceptable: a partial
    source must be distinguishable from a complete one at a glance."""
    from types import SimpleNamespace

    from app.models.sow import SowSourceStatus
    from app.workers.tasks.sow_ledger import _finish_source

    clean = SimpleNamespace()
    _finish_source(None, clean, facts_saved=12, failures=[])
    assert clean.status == SowSourceStatus.done
    assert clean.error_message is None

    partial = SimpleNamespace()
    _finish_source(None, partial, facts_saved=12, failures=["part 3 of 18: boom"])
    assert partial.status == SowSourceStatus.done_with_errors
    assert "part 3 of 18" in partial.error_message
    assert "12 fact(s)" in partial.error_message


# ── T-F-004 / T-F-005 — fact-count overflow ──────────────────────────────────

def _many(n):
    return [
        {"fact_type": "ui_element", "element_type": "button", "label": f"Btn {i}"}
        for i in range(n)
    ]


_CEILING = sow_ledger._MAX_FACTS_PER_CALL


def test_tf004_overflow_raises_for_single_shot_sources():
    """An image or recording has nothing to split, so overflow there is still
    a hard error."""
    with pytest.raises(IngestError) as excinfo:
        sow_ledger._validate_facts(_many(_CEILING + 40), source_label="a screenshot")
    assert str(_CEILING + 40) in str(excinfo.value)
    assert str(_CEILING) in str(excinfo.value)


def test_tf004_overflow_asks_for_a_split_on_chunked_sources():
    """A document chunk CAN be split, so overflow requests that instead of
    failing the source — this is what stops one dense section from taking a
    whole import down with it."""
    with pytest.raises(sow_ledger._NeedsSplit):
        sow_ledger._validate_facts(
            _many(_CEILING + 40), source_label="part 2 of 9", on_overflow="split"
        )


def test_tf004_at_the_ceiling_is_not_an_error():
    facts = sow_ledger._validate_facts(_many(_CEILING), source_label="x")
    assert len(facts) == _CEILING


def test_tf005_truncate_mode_preserves_legacy_behaviour(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        facts = sow_ledger._validate_facts(
            _many(_CEILING + 40), source_label="x", on_overflow="truncate"
        )
    assert len(facts) == _CEILING
    assert any("truncating" in r.message for r in caplog.records)


# ── Dedup is applied at the service layer ────────────────────────────────────

def test_duplicate_facts_across_chunks_are_merged(monkeypatch, fixture_path):
    from app.services import llm_router

    def always_same(prompt, *, system=None, **kw):
        return FakeResult(
            parsed_json={"facts": [{
                "fact_type": "ui_element", "element_type": "button",
                "label": "Bulk delete", "location": None,
                "behavior_notes": None, "source_ref": "§x",
            }]},
            model_used="fake-model",
        )

    monkeypatch.setattr(llm_router, "complete", always_same)
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    facts, _, _, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )
    assert len(facts) == 1, "the same control extracted from every chunk was not merged"


# ── Extraction progress reporting (Attached sources live status) ─────────────
#
# The UI renders a real "part N of M" bar from these callbacks. What matters
# is that the sequence is monotonic, ends at total/total, and — above all —
# that a callback which throws can never cost a completed LLM pass.

def test_progress_reports_every_chunk_in_order(monkeypatch, capture_llm, fixture_path):
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    seen: list[tuple] = []

    facts, _, _, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks,
        file_name="structured.docx",
        max_chars=250,
        on_progress=lambda stage, current, total: seen.append((stage, current, total)),
    )

    assert seen, "no progress was reported"
    assert seen[0] == ("chunking", 0, 0), "chunking must be reported before the total is known"
    extracting = [s for s in seen if s[0] == "extracting"]
    total = extracting[0][2]
    assert total > 1, "fixture must chunk into multiple parts for this test to mean anything"
    # 0 before the first chunk, then one report per completed chunk.
    assert [c for _, c, _ in extracting] == list(range(0, total + 1))
    assert all(t == total for _, _, t in extracting)
    assert seen[-1] == ("saving", total, total)
    assert facts


def test_progress_callback_failure_never_fails_extraction(capture_llm, fixture_path):
    """A progress write is display-only. If it raises (dead DB session, for
    instance), the extraction it was reporting on must still succeed —
    losing paid-for LLM work to a cosmetic UPDATE would be indefensible."""
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")

    def exploding(stage, current, total):
        raise RuntimeError("session is poisoned")

    facts, model, _, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250, on_progress=exploding
    )
    assert facts
    assert model == "fake-model"


def test_progress_is_optional(capture_llm, fixture_path):
    """Omitting on_progress must reproduce the pre-existing behaviour."""
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    facts, _, _, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks, file_name="structured.docx", max_chars=250
    )
    assert facts


def test_progress_reported_for_failed_chunks_too(failing_llm, fixture_path):
    """A run heading for a reported error still advances the bar — a frozen
    bar reads as a hung worker, which is the exact confusion this replaces."""
    failing_llm["fail_if_prompt_contains"] = "Part 2 of"
    blocks = extract_blocks(fixture_path("structured.docx"), "structured.docx")
    seen: list[tuple] = []

    facts, _, failures, _ = sow_ledger.extract_ledger_from_sow_document_full(
        blocks,
        file_name="structured.docx",
        max_chars=250,
        on_progress=lambda stage, current, total: seen.append((stage, current, total)),
    )

    assert failures, "the failed part must still be reported"
    assert facts, "the parts that succeeded must still be saved"
    extracting = [s for s in seen if s[0] == "extracting"]
    total = extracting[0][2]
    assert extracting[-1] == ("extracting", total, total)
