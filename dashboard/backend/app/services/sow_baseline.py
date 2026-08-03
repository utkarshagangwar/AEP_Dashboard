"""Turn an imported SOW document into SOW sections VERBATIM, with no LLM.

Why this exists
---------------
The rest of this pipeline was built for the direction "meeting material ->
SOW": raw input is extracted into a requirements ledger, and an LLM drafts
prose from that ledger. Importing an already-written SOW was bolted onto
that path as just another fact source, which meant a finished document got
shredded into abstract facts and then RE-WRITTEN by a model into a new
document. The user's own words, structure and wording were discarded, and
reaching the skills/TDD extractor required pressing "Generate SOW" on a SOW
that already existed.

That is backwards. An imported SOW is not raw material to synthesise from —
it IS the deliverable. This module maps it onto the same content_blocks
schema the drafting path produces, so it becomes version 1 directly:

  * Deterministic. No model call, so nothing can be paraphrased, dropped,
    reordered or hallucinated. What you uploaded is what you see.
  * Same schema as drafted sections, so the section editor, markdown/docx/pdf
    export, version diff, and the Phase 7 patch/rewrite machinery all keep
    working with no changes at all.
  * Pure passthrough. No AI-written Project Overview, no templated trailing
    sections. A real SOW already has its own scope and acceptance criteria
    (the reference document carries both), and appending AI-written
    near-duplicates of sections the author already wrote is worse than
    appending nothing.

The requirements ledger is still extracted alongside this, because that is
what powers "which sections does a newly attached transcript affect" and
gives rewrite its per-section fact targeting. It is an index over the
document, not a replacement for it.
"""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.services.doc_blocks import Block
from app.services.sow_drafting import _slugify, _unique_key, _validate_block

logger = get_logger(__name__)

# A line that is nothing but a markdown horizontal rule. It is a visual
# separator with no content of its own, and this importer already splits the
# document into sections, so carrying "---" through as a paragraph would add
# noise to every section boundary.
_HORIZONTAL_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")

# Inline markdown that has to come OFF heading text specifically.
#
# A section's `heading` is chrome: it labels the section card, the rewrite
# checkbox list and the version diff, all of which render plain text. Leaving
# the author's "**21\. Demo List Page**" there shows the asterisks and the
# escape backslash literally. Body text is deliberately NOT cleaned this way
# -- there the markers are meaningful and round-trip correctly through the
# markdown export.
_HEADING_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|`)")
_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!])")


def _clean_heading(text: str) -> str:
    """Strip inline emphasis and markdown escapes from a heading."""
    cleaned = _HEADING_EMPHASIS_RE.sub("", text)
    cleaned = _MD_ESCAPE_RE.sub(r"\1", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()

# Headings at or above this level start a new SOW section; deeper headings
# become content inside the current one.
#
# 2, not 1, because real documents are rarely consistent about it. The
# reference SOW numbers its sections 1..223 but writes 1-119 as "##" and
# 120+ as "#" -- an artifact of being stitched together from several
# sources. Splitting on level 1 alone would collapse two thirds of that
# document into a single enormous section; treating levels 1 and 2 as peers
# gives one SOW section per numbered section, which is what the author
# actually wrote.
_SECTION_SPLIT_LEVEL = 2

# Mirror of the caps enforced by sow_drafting._validate_block. Oversized
# content is SPLIT across sibling blocks here rather than being handed to
# the validator to truncate -- a verbatim importer that silently drops the
# 101st table row would defeat its own purpose.
_MAX_TABLE_ROWS = 100
_MAX_LIST_ITEMS = 50
_MAX_PARAGRAPH_CHARS = 5000

_MAX_HEADING_CHARS = 200
_PREAMBLE_HEADING = "Document Header"


def _is_section_start(block: Block) -> bool:
    return (
        block.get("kind") == "heading"
        and int(block.get("level") or 1) <= _SECTION_SPLIT_LEVEL
    )


def _sub_heading_level(level: int) -> int:
    """Heading level for a heading that sits INSIDE a section.

    The section's own title is emitted as level 2 (the convention every
    drafted section follows), so nested headings start at 3. content_blocks
    only models levels 1-4, so anything deeper flattens to 4 rather than
    being dropped.
    """
    return min(max(level, 3), 4)


def _split_table(block: Block) -> list[dict]:
    """One table block per _MAX_TABLE_ROWS rows, header repeated.

    Repeating the header keeps each piece independently readable, exactly
    as doc_chunking does when it has to split a table across chunks.
    """
    header = [str(c) for c in (block.get("header") or [])]
    rows = [[str(c) for c in row] for row in (block.get("rows") or [])]
    if not header and not rows:
        return []
    if not rows:
        # A header-only table still carries meaning (column definitions).
        return [{"type": "table", "headers": header, "rows": [[""] * len(header)]}]

    out: list[dict] = []
    for i in range(0, len(rows), _MAX_TABLE_ROWS):
        out.append({
            "type": "table",
            "headers": header or [""] * len(rows[0]),
            "rows": rows[i : i + _MAX_TABLE_ROWS],
        })
    return out


def _split_paragraph(text: str) -> list[dict]:
    """Long paragraphs become several paragraph blocks, split on sentence
    boundaries where possible so a break never lands mid-word."""
    if len(text) <= _MAX_PARAGRAPH_CHARS:
        return [{"type": "paragraph", "text": text}]

    out: list[dict] = []
    remaining = text
    while remaining:
        if len(remaining) <= _MAX_PARAGRAPH_CHARS:
            out.append({"type": "paragraph", "text": remaining})
            break
        window = remaining[:_MAX_PARAGRAPH_CHARS]
        cut = max(window.rfind(". "), window.rfind("\n"))
        if cut < _MAX_PARAGRAPH_CHARS // 2:
            cut = _MAX_PARAGRAPH_CHARS - 1
        out.append({"type": "paragraph", "text": remaining[: cut + 1].strip()})
        remaining = remaining[cut + 1 :].strip()
    return out


def _flush_list(pending: list[str]) -> list[dict]:
    """Consecutive list_items become bullet_list blocks, capped per block."""
    if not pending:
        return []
    return [
        {"type": "bullet_list", "items": pending[i : i + _MAX_LIST_ITEMS]}
        for i in range(0, len(pending), _MAX_LIST_ITEMS)
    ]


def _blocks_to_content(blocks: list[Block]) -> list[dict]:
    """Map doc_blocks onto content_blocks, preserving document order.

    Every mapped block is passed through sow_drafting._validate_block, the
    same gate the drafting path uses, so an imported section can never carry
    a shape the section editor or the exporters do not understand.
    """
    content: list[dict] = []
    pending_list: list[str] = []

    def flush() -> None:
        nonlocal pending_list
        content.extend(_flush_list(pending_list))
        pending_list = []

    for block in blocks:
        kind = block.get("kind")

        if kind == "list_item":
            text = str(block.get("text") or "").strip()
            if text:
                # Nesting depth is rendered as indentation; content_blocks
                # has no nested-list type, so depth becomes a prefix rather
                # than being lost outright.
                depth = int(block.get("level") or 0)
                pending_list.append(("  " * depth) + text if depth else text)
            continue

        flush()

        if kind == "heading":
            text = _clean_heading(str(block.get("text") or ""))
            if text:
                content.append({
                    "type": "heading",
                    "level": _sub_heading_level(int(block.get("level") or 3)),
                    "text": text,
                })
        elif kind == "paragraph":
            text = str(block.get("text") or "").strip()
            if text and not _HORIZONTAL_RULE_RE.match(text):
                content.extend(_split_paragraph(text))
        elif kind == "code_fence":
            text = str(block.get("text") or "").strip()
            if text:
                # No code block type in the schema; kept as a paragraph so
                # the text survives rather than being discarded.
                content.extend(_split_paragraph(text))
        elif kind == "table":
            content.extend(_split_table(block))
        # page_break carries no content of its own and is intentionally dropped.

    flush()

    validated = [b for b in (_validate_block(b, set()) for b in content) if b]
    dropped = len(content) - len(validated)
    if dropped:
        logger.warning(
            "SOW baseline: %d mapped block(s) failed validation and were skipped", dropped
        )
    return validated


def build_sections_from_document(blocks: list[Block]) -> list[dict]:
    """Split an imported document into [{heading, section_key, content_blocks}].

    Returns [] only when the document has no renderable content at all; the
    caller treats that as "nothing to build a baseline from" and leaves the
    document in its pre-import state rather than creating an empty version.
    """
    if not blocks:
        return []

    # Group blocks into runs, each starting at a section-level heading.
    groups: list[tuple[str | None, list[Block]]] = []
    current_heading: str | None = None
    current: list[Block] = []

    for block in blocks:
        if _is_section_start(block):
            if current_heading is not None or current:
                groups.append((current_heading, current))
            current_heading = _clean_heading(str(block.get("text") or "")) or None
            current = []
        else:
            current.append(block)
    if current_heading is not None or current:
        groups.append((current_heading, current))

    used_keys: set[str] = set()
    sections: list[dict] = []

    for heading, body in groups:
        # Content appearing before the document's first heading still has to
        # land somewhere -- dropping a document's preamble would be exactly
        # the silent loss this module exists to prevent.
        title = (heading or _PREAMBLE_HEADING)[:_MAX_HEADING_CHARS]
        content = _blocks_to_content(body)

        if not content and heading is None:
            continue  # genuinely empty preamble, nothing to preserve

        # Every section leads with its own title block, matching what
        # sow_drafting.draft_section produces, so rendering and export treat
        # imported and drafted sections identically.
        content.insert(0, {"type": "heading", "level": 2, "text": title})

        sections.append({
            "heading": title,
            "section_key": _unique_key(_slugify(title), used_keys),
            "content_blocks": content,
        })

    logger.info(
        "SOW baseline: mapped %d block(s) into %d verbatim section(s)",
        len(blocks), len(sections),
    )
    return sections
