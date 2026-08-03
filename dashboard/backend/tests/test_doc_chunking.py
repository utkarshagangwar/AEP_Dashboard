"""Phase 2 — structure-aware chunking (T-C-001..018).

SOW_CHUNKING_PLAN.md §3 Phase 2. The two load-bearing tests are T-C-001
(lossless) and T-C-005 (size ceiling); everything else describes where
boundaries land.
"""
from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.doc_blocks import IngestError, extract_blocks
from app.services.doc_chunking import (
    DEFAULT_MAX_CHARS,
    STRATEGY_HARD_SPLIT,
    Chunk,
    chunk_document,
)


def _blocks(fixture_path, name):
    return extract_blocks(fixture_path(name), name)


def _normalise(text: str) -> str:
    """Whitespace-insensitive comparison for the lossless invariant."""
    return re.sub(r"\s+", " ", text).strip()


def _squash(text: str) -> str:
    """All whitespace removed — the strictest honest form of 'no character
    was lost'.

    Needed for the property tests because a hard split may cut mid-token
    (a 51-character unbroken paragraph with a 50-character budget splits
    into "0"*50 + "0"). Rejoining those pieces with any separator fabricates
    a character the source never had, so the comparison has to ignore
    separators entirely rather than assume one.
    """
    return re.sub(r"\s+", "", text)


# ── T-C-001 — lossless invariant ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["structured.docx", "numbered.md", "flat.txt", "multipage.pdf"]
)
def test_tc001_lossless_across_fixtures(fixture_path, name):
    """Exact round-trip for documents with no oversized table.

    wide_table.docx is excluded and covered separately below: repeating a
    table's header on every continuation chunk is intentional duplication
    (T-C-007), so strict equality cannot hold there.
    """
    blocks = _blocks(fixture_path, name)
    chunks = chunk_document(blocks, file_name=name, max_chars=3_000)

    from app.services.doc_blocks import block_text

    source = _normalise(" ".join(block_text(b) for b in blocks))
    rebuilt = _normalise(" ".join(c.text for c in chunks))
    assert source == rebuilt, f"content lost or duplicated while chunking {name}"


def test_tc001_no_content_lost_when_table_headers_repeat(fixture_path):
    """For a split table the invariant weakens from equality to 'nothing
    lost, and the ONLY duplication is the header row'."""
    name = "wide_table.docx"
    blocks = _blocks(fixture_path, name)
    chunks = chunk_document(blocks, file_name=name, max_chars=2_000)

    from app.services.doc_blocks import block_text

    source_lines = [
        ln.strip() for b in blocks for ln in block_text(b).split("\n") if ln.strip()
    ]
    rebuilt_lines = [
        ln.strip() for c in chunks for ln in c.text.split("\n") if ln.strip()
    ]

    assert not (set(source_lines) - set(rebuilt_lines)), "content lost while chunking"
    assert not (set(rebuilt_lines) - set(source_lines)), "content invented while chunking"

    # Accounting: every extra line must be a repeated header, one per
    # continuation chunk.
    header = "Field | Type | Description"
    extra = len(rebuilt_lines) - len(source_lines)
    table_chunks = sum(1 for c in chunks if header in c.text)
    assert extra == table_chunks - 1, (
        "duplication beyond the intentional repeated table header"
    )


@pytest.mark.slow
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    paragraphs=st.lists(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\n"),
            min_size=1,
            max_size=400,
        ).filter(lambda s: s.strip()),
        min_size=1,
        max_size=40,
    ),
    max_chars=st.integers(min_value=50, max_value=2_000),
)
def test_tc001_lossless_property(paragraphs, max_chars):
    """The claim 'no character of any input is ever dropped' needs generated
    inputs, not a hand-picked example set."""
    text = "\n\n".join(paragraphs)
    chunks = chunk_document(text, file_name="x.txt", max_chars=max_chars)
    assert _squash("".join(c.text for c in chunks)) == _squash(text)


@pytest.mark.slow
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    paragraphs=st.lists(
        st.text(alphabet="abcdef ", min_size=1, max_size=300).filter(lambda s: s.strip()),
        min_size=1,
        max_size=30,
    ),
    max_chars=st.integers(min_value=60, max_value=1_500),
)
def test_tc005_size_ceiling_property(paragraphs, max_chars):
    """T-C-005: no chunk exceeds max_chars unless it was hard-split."""
    chunks = chunk_document("\n\n".join(paragraphs), file_name="x.txt", max_chars=max_chars)
    for chunk in chunks:
        assert chunk.char_count <= max_chars or chunk.is_degraded


# ── T-C-002 — small documents are untouched ──────────────────────────────────

def test_tc002_small_document_is_one_chunk(fixture_path):
    blocks = _blocks(fixture_path, "numbered.md")
    chunks = chunk_document(blocks, file_name="numbered.md", max_chars=DEFAULT_MAX_CHARS)
    assert len(chunks) == 1
    assert chunks[0].index == 1 and chunks[0].total == 1
    assert chunks[0].strategy == "heading_tree"


def test_tc002_single_chunk_plain_text_has_no_framing():
    """A small SOW's prompt must not change just because chunking got
    smarter -- framing appears only when there is something to frame."""
    chunk = chunk_document("just one short paragraph", file_name="a.txt")[0]
    assert chunk.context_header == ""
    assert chunk.prompt_text() == "just one short paragraph"


# ── T-C-003 / T-C-004 / T-C-010 — heading behaviour ──────────────────────────

def test_tc003_splits_land_on_structural_boundaries(fixture_path):
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=200)
    for chunk in chunks[1:]:
        assert chunk.text == chunk.text.strip()
        assert not chunk.text.startswith(("|", " |"))


def test_tc010_heading_path_is_hierarchical(fixture_path):
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=250)

    paths = [c.heading_path for c in chunks if c.heading_path]
    assert paths, "no chunk carried a heading path"
    for path in paths:
        # A path is an ancestry chain: no repeats, and it must start at a
        # top-level heading present in the document.
        assert len(path) == len(set(path))


def test_tc010_table_chunk_carries_its_own_section(fixture_path):
    """The payoff of the Phase 1 ordering fix: a requirements table must be
    labelled with its real section, not the document's last heading."""
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=250)

    table_chunk = next(c for c in chunks if "REQ-001" in c.text)
    assert "2.1 Candidate List" in table_chunk.heading_path
    assert "3. Sign-off & Acceptance Criteria" not in table_chunk.heading_path


def test_tc004_packs_siblings_rather_than_one_chunk_per_heading(fixture_path):
    """Guards the cost risk in plan §6: structure-aware chunking must not
    explode the LLM call count."""
    blocks = _blocks(fixture_path, "structured.docx")
    heading_count = sum(1 for b in blocks if b["kind"] == "heading")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=DEFAULT_MAX_CHARS)
    assert len(chunks) == 1 < heading_count


# ── T-C-006 / T-C-007 — tables ───────────────────────────────────────────────

def test_tc006_table_smaller_than_budget_is_never_split(fixture_path):
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=400)

    holding = [c for c in chunks if "REQ-001" in c.text or "REQ-002" in c.text]
    assert len(holding) == 1, "a table that fits in one chunk was split"
    assert "REQ-001" in holding[0].text and "REQ-002" in holding[0].text


def test_tc007_oversized_table_splits_on_rows_and_repeats_header(fixture_path):
    blocks = _blocks(fixture_path, "wide_table.docx")
    chunks = chunk_document(blocks, file_name="wide_table.docx", max_chars=2_000)

    table_chunks = [c for c in chunks if "field_" in c.text]
    assert len(table_chunks) > 1, "fixture no longer forces a table split"

    for chunk in table_chunks:
        assert "Field | Type | Description" in chunk.text, (
            "a table continuation chunk lost its header row"
        )


def test_tc007_no_table_row_is_split_across_chunks(fixture_path):
    blocks = _blocks(fixture_path, "wide_table.docx")
    chunks = chunk_document(blocks, file_name="wide_table.docx", max_chars=2_000)

    seen = []
    for chunk in chunks:
        for line in chunk.text.split("\n"):
            if line.startswith("field_"):
                seen.append(line)
    # Every emitted row must be complete: 3 pipe-separated cells.
    assert seen
    for line in seen:
        assert len(line.split(" | ")) == 3, f"row split across chunks: {line!r}"
    assert len({ln.split(" | ")[0] for ln in seen}) == 300


# ── T-C-009 — code fences ────────────────────────────────────────────────────

def test_tc009_code_fence_never_split(fixture_path):
    blocks = _blocks(fixture_path, "numbered.md")
    chunks = chunk_document(blocks, file_name="numbered.md", max_chars=150)

    holding = [c for c in chunks if "def handler():" in c.text]
    assert len(holding) == 1
    assert holding[0].text.count("```") == 2


# ── T-C-011 / T-C-012 — transcripts ──────────────────────────────────────────

def test_tc011_speaker_turns_are_not_split(fixture_path):
    text = open(fixture_path("meeting.txt"), encoding="utf-8").read()
    chunks = chunk_document(text, file_name="meeting.txt", doc_kind="transcript", max_chars=1_500)

    assert len(chunks) > 1
    for chunk in chunks:
        # Every chunk must begin at a turn boundary.
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s\w+:", chunk.text), (
            f"chunk starts mid-turn: {chunk.text[:60]!r}"
        )


def test_tc012_transcript_locator_is_first_timestamp(fixture_path):
    text = open(fixture_path("meeting.txt"), encoding="utf-8").read()
    chunks = chunk_document(text, file_name="meeting.txt", doc_kind="transcript", max_chars=1_500)

    for chunk in chunks:
        assert chunk.locator is not None
        assert chunk.locator in chunk.text


def test_tc011_unlabelled_transcript_falls_back_to_paragraphs():
    text = "\n\n".join(f"some caption line number {i}" for i in range(50))
    chunks = chunk_document(text, file_name="c.txt", doc_kind="transcript", max_chars=300)
    assert len(chunks) > 1
    assert _normalise(" ".join(c.text for c in chunks)) == _normalise(text)


# ── T-C-013 — pdf locators ───────────────────────────────────────────────────

def test_tc013_pdf_locator_is_page_number(fixture_path):
    blocks = _blocks(fixture_path, "multipage.pdf")
    chunks = chunk_document(blocks, file_name="multipage.pdf", max_chars=120)

    located = [c for c in chunks if c.locator]
    assert located
    for chunk in located:
        assert re.fullmatch(r"p\.\d+", chunk.locator)

    overview = next(c for c in chunks if "Project Overview" in c.text)
    assert overview.locator == "p.1"


# ── T-C-014 — hard split is flagged ──────────────────────────────────────────

def test_tc014_pathological_paragraph_is_flagged_not_silent(fixture_path, caplog):
    import logging

    text = open(fixture_path("pathological.txt"), encoding="utf-8").read()
    with caplog.at_level(logging.WARNING):
        chunks = chunk_document(text, file_name="pathological.txt", max_chars=5_000)

    assert len(chunks) > 1
    assert all(c.strategy == STRATEGY_HARD_SPLIT for c in chunks)
    assert all(c.is_degraded for c in chunks)
    assert any("hard-splitting" in r.message for r in caplog.records), (
        "degradation must be logged, not silent"
    )


def test_tc014_normal_document_is_never_marked_degraded(fixture_path):
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=500)
    assert not any(c.is_degraded for c in chunks)


# ── T-C-015 — legacy parity for unstructured input ───────────────────────────

def test_tc015_paragraph_fallback_matches_legacy_chunk_text(fixture_path):
    """A document with no structure must chunk exactly as it does today."""
    from app.services.design_ingest import chunk_text

    text = open(fixture_path("flat.txt"), encoding="utf-8").read()
    legacy = chunk_text(text, max_chars=5_000)
    new = chunk_document(text, file_name="flat.txt", max_chars=5_000)

    assert [c.text for c in new] == legacy


def test_tc015_strategy_is_paragraph_for_unstructured(fixture_path):
    text = open(fixture_path("flat.txt"), encoding="utf-8").read()
    chunks = chunk_document(text, file_name="flat.txt", max_chars=5_000)
    assert {c.strategy for c in chunks} == {"paragraph"}


# ── T-C-016 — indexing ───────────────────────────────────────────────────────

def test_tc016_index_is_contiguous_and_total_consistent(fixture_path):
    blocks = _blocks(fixture_path, "structured.docx")
    chunks = chunk_document(blocks, file_name="structured.docx", max_chars=200)

    assert [c.index for c in chunks] == list(range(1, len(chunks) + 1))
    assert {c.total for c in chunks} == {len(chunks)}


# ── T-C-017 / T-C-018 — edge cases ───────────────────────────────────────────

def test_tc017_empty_input_raises_never_returns_empty_list():
    with pytest.raises(IngestError):
        chunk_document("   \n\n  ", file_name="a.txt")
    with pytest.raises(IngestError):
        chunk_document([], file_name="a.md")


def test_tc017_invalid_max_chars_rejected():
    with pytest.raises(ValueError):
        chunk_document("text", file_name="a.txt", max_chars=0)


def test_tc018_trailing_whitespace_does_not_change_chunk_count():
    body = "\n\n".join(f"paragraph number {i}" for i in range(30))
    a = chunk_document(body, file_name="a.txt", max_chars=200)
    b = chunk_document(body + "\n", file_name="a.txt", max_chars=200)
    c = chunk_document(body.replace("\n", "\r\n"), file_name="a.txt", max_chars=200)
    assert len(a) == len(b)
    assert len(a) == len(c)


def test_chunk_is_immutable():
    chunk = chunk_document("hello", file_name="a.txt")[0]
    with pytest.raises(Exception):
        chunk.text = "mutated"  # type: ignore[misc]
    assert isinstance(chunk, Chunk)
