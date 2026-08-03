"""An imported SOW becomes version 1 verbatim — no LLM, nothing lost.

The defect these cover: importing an already-written SOW used to shred it
into abstract facts and have a model re-write it into a new document. The
author's structure, wording and section numbering were discarded, and
reaching the skills extractor required pressing "Generate SOW" on a SOW
that already existed.

Everything here is deterministic — sow_baseline never calls a model — so
these tests assert exact content preservation rather than "roughly right".
"""
from __future__ import annotations

import re

from app.services.sow_baseline import (
    _clean_heading,
    _SECTION_SPLIT_LEVEL,
    build_sections_from_document,
)


def _h(text, level=2):
    return {"kind": "heading", "level": level, "text": text}


def _p(text):
    return {"kind": "paragraph", "text": text}


def _li(text, level=0):
    return {"kind": "list_item", "level": level, "text": text}


_DOC = [
    _h("**1\\. Feature Overview**", 2),
    _p("The platform does things."),
    _li("First bullet"),
    _li("Second bullet"),
    _h("1.1 Sub Detail", 3),
    _p("Nested detail."),
    _h("**2\\. Demo List Page**", 2),
    {"kind": "table", "header": ["Column", "Editability"],
     "rows": [["ID", "Read-only"], ["Phone", "Editable"]]},
]


# ── Structure ────────────────────────────────────────────────────────────────

def test_each_top_level_heading_becomes_its_own_section():
    secs = build_sections_from_document(_DOC)
    assert [s["heading"] for s in secs] == ["1. Feature Overview", "2. Demo List Page"]


def test_deeper_headings_stay_inside_their_section():
    """A level-3 heading is content, not a section boundary — otherwise a
    document's subsections would shatter its structure."""
    secs = build_sections_from_document(_DOC)
    first = secs[0]["content_blocks"]
    assert any(b["type"] == "heading" and b["text"] == "1.1 Sub Detail" for b in first)
    assert len(secs) == 2


def test_section_keys_are_unique_even_for_repeated_headings():
    dup = [_h("Overview", 2), _p("a"), _h("Overview", 2), _p("b")]
    secs = build_sections_from_document(dup)
    keys = [s["section_key"] for s in secs]
    assert len(set(keys)) == len(keys) == 2


def test_every_section_leads_with_its_own_title_block():
    """Matches what sow_drafting.draft_section produces, so imported and
    drafted sections render and export identically."""
    for s in build_sections_from_document(_DOC):
        head = s["content_blocks"][0]
        assert head["type"] == "heading" and head["level"] == 2
        assert head["text"] == s["heading"]


def test_content_before_the_first_heading_is_preserved():
    """Dropping a document's preamble would be exactly the silent loss this
    module exists to prevent."""
    secs = build_sections_from_document([_p("Preamble text."), _h("1. Real", 2), _p("body")])
    assert secs[0]["heading"] == "Document Header"
    assert any("Preamble text." in b.get("text", "") for b in secs[0]["content_blocks"])


def test_a_document_with_no_headings_still_produces_a_section():
    secs = build_sections_from_document([_p("Just prose."), _p("More prose.")])
    assert len(secs) == 1
    assert any("Just prose." in b.get("text", "") for b in secs[0]["content_blocks"])


def test_empty_input_produces_nothing():
    assert build_sections_from_document([]) == []


# ── Content fidelity ─────────────────────────────────────────────────────────

def test_consecutive_list_items_become_one_bullet_list():
    secs = build_sections_from_document(_DOC)
    lists = [b for b in secs[0]["content_blocks"] if b["type"] == "bullet_list"]
    assert len(lists) == 1
    assert lists[0]["items"] == ["First bullet", "Second bullet"]


def test_tables_keep_their_headers_and_rows():
    secs = build_sections_from_document(_DOC)
    tables = [b for b in secs[1]["content_blocks"] if b["type"] == "table"]
    assert len(tables) == 1
    assert tables[0]["headers"] == ["Column", "Editability"]
    assert ["Phone", "Editable"] in tables[0]["rows"]


def test_oversized_tables_split_rather_than_truncate():
    """_validate_block caps a table at 100 rows. A verbatim importer that
    let the 101st row be silently dropped would defeat its own purpose."""
    big = {"kind": "table", "header": ["n"], "rows": [[str(i)] for i in range(250)]}
    secs = build_sections_from_document([_h("T", 2), big])
    tables = [b for b in secs[0]["content_blocks"] if b["type"] == "table"]
    assert len(tables) == 3
    assert sum(len(t["rows"]) for t in tables) == 250
    assert all(t["headers"] == ["n"] for t in tables), "header repeats on every piece"


def test_oversized_lists_split_rather_than_truncate():
    items = [_li(f"item {i}") for i in range(130)]
    secs = build_sections_from_document([_h("L", 2), *items])
    lists = [b for b in secs[0]["content_blocks"] if b["type"] == "bullet_list"]
    assert sum(len(b["items"]) for b in lists) == 130


def test_oversized_paragraphs_split_rather_than_truncate():
    long_text = ("Sentence number one is here. " * 400).strip()
    secs = build_sections_from_document([_h("P", 2), _p(long_text)])
    paras = [b for b in secs[0]["content_blocks"] if b["type"] == "paragraph"]
    assert len(paras) > 1
    joined = re.sub(r"\W+", "", "".join(p["text"] for p in paras))
    assert joined == re.sub(r"\W+", "", long_text), "text was lost while splitting"


def test_horizontal_rules_are_dropped_but_real_content_is_not():
    secs = build_sections_from_document([_h("S", 2), _p("---"), _p("Real content.")])
    texts = [b.get("text", "") for b in secs[0]["content_blocks"]]
    assert "---" not in texts
    assert any("Real content." in t for t in texts)


# ── Heading cleanup ──────────────────────────────────────────────────────────

def test_heading_markdown_is_stripped():
    """`heading` labels the section card, the rewrite checkbox and the diff —
    all plain-text chrome where markup shows literally."""
    assert _clean_heading("**21\\. Demo List Page**") == "21. Demo List Page"
    assert _clean_heading("*Italic*") == "Italic"
    assert _clean_heading("`code`") == "code"
    assert _clean_heading("  Spaced   Out  ") == "Spaced Out"


def test_body_text_keeps_its_inline_markdown():
    """Unlike headings, body emphasis is meaningful and round-trips through
    the markdown export — stripping it would lose the author's formatting."""
    secs = build_sections_from_document([_h("S", 2), _p("The **bold** part.")])
    body = [b for b in secs[0]["content_blocks"] if b["type"] == "paragraph"]
    assert body[0]["text"] == "The **bold** part."


def test_split_level_treats_h1_and_h2_as_peers():
    """Real documents are inconsistent: the reference SOW numbers sections
    1..223 but writes 1-119 as '##' and 120+ as '#'. Splitting on level 1
    alone would collapse two thirds of it into one section."""
    assert _SECTION_SPLIT_LEVEL == 2
    mixed = [_h("A", 1), _p("a"), _h("B", 2), _p("b"), _h("C", 1), _p("c")]
    assert len(build_sections_from_document(mixed)) == 3
