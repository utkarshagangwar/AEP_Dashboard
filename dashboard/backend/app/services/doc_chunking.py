"""Structure-aware document chunking.

SOW_CHUNKING_PLAN.md Phase 2. Splits a document into LLM-sized parts at
real structural boundaries -- never mid-table, never mid-clause, never
mid-code-fence -- and labels every part with the section it came from.

REPLACES design_ingest.chunk_text(), which split purely on "\\n\\n" and a
character count. That splitter had two defects this module fixes:

  D1 BOUNDARY LOSS. A fixed 20,000-char window cuts through requirements
     and tables. A requirement split across parts 3 and 4 is extracted as
     two half-facts, or dropped.
  D2 NO POSITIONAL CONTEXT. Each part was labelled only "part 3 of 7". The
     model had no idea which section it was reading, so it either invented
     a `location` for every ui_element fact or left it null where the
     enclosing heading made it obvious.

WHY STRUCTURE-AWARE AND NOT EMBEDDING-BASED
-------------------------------------------
Published benchmarks favour "semantic chunking" because it recovers
document structure that a naive splitter destroyed. In a .docx or .md SOW
that structure is already explicit -- heading levels, table boundaries,
numbered clauses. Reading it directly (app.services.doc_blocks) is strictly
more accurate than inferring it from embedding similarity, costs nothing
per document, and is deterministic, which is what makes the ~30 assertions
in tests/test_doc_chunking.py possible at all. See plan §1 for the full
options table.

Note also that this pipeline has NO retrieval step: it is map/reduce, every
chunk is sent to the LLM and the results are concatenated. There is no
top-k selection for better embeddings to improve.

HARD RULES (enforced by every strategy, asserted in the test suite)
-------------------------------------------------------------------
1. Every character of the input appears in exactly one chunk's `text`.
   Overlap lives only in `context_header`, never in `text`. (T-C-001)
2. No chunk exceeds max_chars unless strategy == "hard_split". (T-C-005)
3. Tables are never split mid-row; an oversized table splits on row
   boundaries and REPEATS its header on every continuation. (T-C-006/007)
4. Numbered clauses and fenced code blocks are never split. (T-C-008/009)
5. An atomic unit larger than max_chars is hard-split, but tagged
   strategy="hard_split" and logged at WARNING. Degradation is always
   observable, never silent. (T-C-014)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from app.core.logging import get_logger
from app.services.doc_blocks import Block, IngestError, block_text

logger = get_logger(__name__)

DocKind = Literal["sow_document", "transcript", "checkpoints_sow"]

# Fallback budget, unchanged from design_ingest._CHUNK_MAX_CHARS. Used only
# when a caller names no doc_kind-specific budget below.
DEFAULT_MAX_CHARS = 20_000

# Per-doc-kind budgets. This was previously one flat 20,000 for everything,
# sized against how much INPUT a model can accept. That was the wrong
# constraint and it was silently losing content: the binding limit is the
# OUTPUT budget of whatever extracts from the chunk.
#
# Dense SOW prose yields roughly one ledger fact per 100-200 characters, and
# one fact serialises to ~60-120 output tokens once behavior_notes and
# source_ref are filled in. So:
#
#   20,000 chars -> 100-200 facts -> ~9k-24k output tokens   (over budget, always)
#    8,000 chars ->  40-80  facts -> ~4k-9k  output tokens   (fits, with headroom)
#
# When the output is cut off, llm_router's JSON repair pass rewrites the
# truncated array into *valid* JSON by dropping the tail — a clean-looking
# response with most of the section missing. Sizing the chunk so the response
# fits is the primary fix; llm_router.complete_json_complete and
# sow_ledger._extract_with_split are the safety nets behind it.
#
# Transcripts are far less fact-dense per character (speech is verbose), so
# they keep a larger budget. Both SOW budgets are env-overridable so spend can
# be tuned against real documents without a deploy.
MAX_CHARS_BY_DOC_KIND: dict[str, int] = {
    "sow_document": int(os.environ.get("SOW_CHUNK_MAX_CHARS", "").strip() or 8_000),
    "checkpoints_sow": int(
        os.environ.get("SOW_CHECKPOINT_CHUNK_MAX_CHARS", "").strip() or 8_000
    ),
    "transcript": int(os.environ.get("SOW_TRANSCRIPT_CHUNK_MAX_CHARS", "").strip() or 16_000),
}


def max_chars_for(doc_kind: str | None) -> int:
    """Chunk budget for a doc kind, falling back to DEFAULT_MAX_CHARS.

    Callers pass max_chars=None to get this; an explicit max_chars always
    wins, so existing callers and tests that name a budget are unaffected.
    """
    return MAX_CHARS_BY_DOC_KIND.get(doc_kind or "", DEFAULT_MAX_CHARS)

# How much of the previous chunk is replayed as read-only context. Large
# enough to resolve "the button above" / "this dropdown"; small enough that
# it cannot dominate the prompt.
CONTEXT_TAIL_CHARS = 500

STRATEGY_HARD_SPLIT = "hard_split"


@dataclass(frozen=True)
class Chunk:
    """One LLM-sized part of a document.

    `text` is the extractable content and nothing else. `context_header` is
    prompt framing built from the surrounding document; it is deliberately
    NOT part of `text` so the lossless invariant (T-C-001) stays checkable
    and so overlap can never be mistaken for new content to extract.
    """

    index: int                                  # 1-based
    total: int
    text: str
    heading_path: list[str] = field(default_factory=list)
    locator: str | None = None
    strategy: str = "paragraph"
    context_header: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def is_degraded(self) -> bool:
        """True when this chunk had to be cut at an arbitrary point. Surfaced
        in the UI (plan §5) so the degradation is testable, not log-only."""
        return self.strategy == STRATEGY_HARD_SPLIT

    def prompt_text(self) -> str:
        """Exactly what gets sent to the LLM: framing, then content."""
        return f"{self.context_header}\n\n<content>\n{self.text}\n</content>" \
            if self.context_header else self.text


# ── Units: the atoms a document is assembled from ────────────────────────────


@dataclass
class _Unit:
    """An indivisible span of document text plus where it sits.

    Chunking is: turn blocks into units (each carrying its heading path),
    then greedily pack units into chunks without ever splitting one. The
    only exception is a single unit larger than max_chars, which is
    hard-split and flagged.
    """

    text: str
    heading_path: list[str]
    locator: str | None
    splittable: bool = True     # False for tables/code fences handled specially
    is_heading: bool = False
    heading_level: int = 0
    table: Block | None = None  # set when this unit is a whole table

    @property
    def size(self) -> int:
        return len(self.text)


def _units_from_blocks(blocks: list[Block]) -> list[_Unit]:
    """Flatten blocks into units, threading the running heading path and page
    locator through them. This is where structure becomes position."""
    units: list[_Unit] = []
    heading_stack: list[tuple[int, str]] = []
    page: int | None = None

    for block in blocks:
        kind = block["kind"]

        if kind == "page_break":
            page = block["page"]
            continue

        if kind == "heading":
            level = block["level"]
            # Pop siblings and deeper levels; what remains is the ancestry.
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, block["text"]))
            units.append(_Unit(
                text=block["text"],
                heading_path=[t for _, t in heading_stack],
                locator=f"p.{page}" if page else None,
                is_heading=True,
                heading_level=level,
            ))
            continue

        path = [t for _, t in heading_stack]
        locator = f"p.{page}" if page else None

        if kind == "table":
            units.append(_Unit(
                text=block_text(block),
                heading_path=path,
                locator=locator,
                splittable=False,
                table=block,
            ))
            continue

        if kind == "code_fence":
            units.append(_Unit(
                text=block["text"],
                heading_path=path,
                locator=locator,
                splittable=False,
            ))
            continue

        units.append(_Unit(text=block_text(block), heading_path=path, locator=locator))

    return units


# ── Table splitting ──────────────────────────────────────────────────────────


def _split_table_unit(unit: _Unit, max_chars: int) -> list[_Unit]:
    """Split an oversized table on ROW boundaries, repeating the header.

    A table is the single most common place a SOW hides its functional
    requirements (see sow_import's module docstring), and a row cut in half
    loses the mapping between a requirement ID and its behaviour. Repeating
    the header is what keeps each continuation chunk self-describing --
    without it, chunk 2 of a data dictionary is an unlabelled grid.
    """
    table = unit.table or {}
    header = table.get("header") or []
    rows = table.get("rows") or []

    header_line = " | ".join(header) if header else ""
    header_cost = len(header_line) + 1 if header_line else 0

    parts: list[_Unit] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        body = "\n".join(current)
        text = f"{header_line}\n{body}" if header_line else body
        parts.append(_Unit(
            text=text,
            heading_path=list(unit.heading_path),
            locator=unit.locator,
            splittable=False,
        ))
        current, current_len = [], 0

    for row in rows:
        line = " | ".join(row)
        # A single row bigger than the budget cannot be packed with anything;
        # emit it alone and let the caller's hard-split guard flag it.
        if len(line) + header_cost > max_chars:
            flush()
            parts.append(_Unit(
                text=f"{header_line}\n{line}" if header_line else line,
                heading_path=list(unit.heading_path),
                locator=unit.locator,
                splittable=False,
            ))
            continue
        added = len(line) + (1 if current else 0)
        if current and header_cost + current_len + added > max_chars:
            flush()
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += added

    flush()
    return parts or [unit]


# ── Packing ──────────────────────────────────────────────────────────────────


def _hard_split(unit: _Unit, max_chars: int) -> list[_Unit]:
    """Last resort for a single unit that exceeds the budget on its own.

    Logged at WARNING with its section, and every resulting piece is marked
    so the degradation reaches the UI rather than dying in worker logs.
    """
    logger.warning(
        "doc_chunking: hard-splitting a %d-char unit that exceeds the %d-char "
        "budget on its own (section: %s). Extraction quality at these "
        "boundaries is degraded.",
        unit.size, max_chars, " > ".join(unit.heading_path) or "<no section>",
    )
    return [
        _Unit(
            text=unit.text[i:i + max_chars],
            heading_path=list(unit.heading_path),
            locator=unit.locator,
            splittable=False,
        )
        for i in range(0, unit.size, max_chars)
    ]


def _normalise_units(units: list[_Unit], max_chars: int) -> tuple[list[_Unit], bool]:
    """Ensure no unit exceeds max_chars. Returns (units, any_hard_split)."""
    out: list[_Unit] = []
    degraded = False
    for unit in units:
        if unit.size <= max_chars:
            out.append(unit)
            continue
        if unit.table is not None:
            pieces = _split_table_unit(unit, max_chars)
            for piece in pieces:
                if piece.size > max_chars:
                    out.extend(_hard_split(piece, max_chars))
                    degraded = True
                else:
                    out.append(piece)
            continue
        out.extend(_hard_split(unit, max_chars))
        degraded = True
    return out, degraded


def _pack(units: list[_Unit], max_chars: int) -> list[list[_Unit]]:
    """Greedily group units into chunks, preferring to break before the
    shallowest heading available.

    Packing sibling sections together rather than emitting one chunk per
    heading is what keeps the LLM call count flat (plan risk: "structure-
    aware chunks are smaller -> more calls"). A new chunk starts only when
    the next unit genuinely does not fit, or when a top-level heading offers
    a clean break and the current chunk is already substantial.
    """
    if not units:
        return []

    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    current_len = 0
    # Below this fill ratio, a heading is not worth breaking on -- otherwise
    # a document of many short sections produces many tiny chunks.
    break_threshold = max_chars * 0.6

    for unit in units:
        added = unit.size + (2 if current else 0)
        must_break = current and current_len + added > max_chars
        wants_break = (
            current
            and unit.is_heading
            and unit.heading_level <= 2
            and current_len >= break_threshold
        )
        if must_break or wants_break:
            groups.append(current)
            current, current_len = [unit], unit.size
        else:
            current.append(unit)
            current_len += added

    if current:
        groups.append(current)
    return groups


# ── Heading path for a group ─────────────────────────────────────────────────


def _group_heading_path(group: list[_Unit]) -> list[str]:
    """The section a chunk belongs to.

    Uses the path of the first NON-heading unit, so a chunk that opens with
    "## 4.3 Bulk Actions" is labelled as being in 4.3 rather than in its
    parent. Falls back to the first unit's path for a chunk of pure
    headings.
    """
    for unit in group:
        if not unit.is_heading:
            return list(unit.heading_path)
    return list(group[0].heading_path) if group else []


def _group_locator(group: list[_Unit]) -> str | None:
    for unit in group:
        if unit.locator:
            return unit.locator
    return None


# ── Context header ───────────────────────────────────────────────────────────


def _render_context_header(
    *,
    document_title: str | None,
    heading_path: list[str],
    locator: str | None,
    index: int,
    total: int,
    start_char: int,
    end_char: int,
    total_chars: int,
    preceding_tail: str,
) -> str:
    """The framing block prepended to each chunk's prompt (plan §2.3).

    The <preceding_context> fence is the reason this is worth doing at all:
    it gives the model enough continuity to resolve "the button above"
    WITHOUT giving it license to extract facts twice. Chunk overlap without
    that instruction trades boundary loss for duplicate facts.
    """
    if total == 1 and not heading_path:
        # A small single-chunk document gains nothing from framing, and
        # adding it would change the prompt for every small SOW that works
        # fine today.
        return ""

    lines = ["<document_context>"]
    if document_title:
        lines.append(f"Document: {document_title}")
    if heading_path:
        lines.append(f"Section path: {' > '.join(heading_path)}")
    lines.append(
        f"Part {index} of {total} (characters {start_char:,}-{end_char:,} "
        f"of {total_chars:,})"
    )
    if locator:
        lines.append(f"Locator: {locator}")
    lines.append("</document_context>")

    if preceding_tail:
        lines.extend([
            "",
            '<preceding_context reason="continuity only">',
            "DO NOT extract facts from this block. It is the tail of the "
            "previous part, provided only so you can resolve references like "
            '"the button above" or "this dropdown". Every fact you extract '
            "must come from the <content> block below.",
            "...",
            preceding_tail,
            "</preceding_context>",
        ])

    return "\n".join(lines)


# ── Strategy selection ───────────────────────────────────────────────────────

_TRANSCRIPT_SPEAKER_RE = re.compile(
    r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?([A-Z][\w .'-]{0,40}):\s"
)
_TIMESTAMP_RE = re.compile(r"\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?")


def _strategy_for(file_name: str, doc_kind: DocKind, blocks: list[Block]) -> str:
    if doc_kind == "transcript":
        return "speaker_turn"
    ext = os.path.splitext(file_name.lower())[1]
    if ext == ".docx":
        return "docx_structure"
    if ext == ".pdf":
        return "page_heading"
    if any(b["kind"] == "heading" for b in blocks):
        return "heading_tree"
    return "paragraph"


# ── Transcript chunking ──────────────────────────────────────────────────────


def _paragraph_units(text: str) -> list[_Unit]:
    """Blank-line paragraphs — the fallback for text with no structure.

    Deliberately does NOT strip each paragraph. design_ingest.chunk_text()
    preserves surrounding whitespace, and tests/test_doc_chunking.py
    (T-C-015) asserts byte-for-byte parity with it for unstructured input:
    a document that has no structure to exploit must chunk exactly as it
    does today, so the already-shipped SOW Checkpoints pipeline sees no
    behaviour change. Stripping here broke that parity.
    """
    return [_Unit(text=p, heading_path=[], locator=None) for p in text.split("\n\n")]


def _transcript_units(text: str) -> list[_Unit]:
    """One unit per speaker turn.

    A turn is the atomic unit of a meeting: splitting one in half separates
    a decision from the person who made it and from the qualifier that
    followed. Falls back to blank-line paragraphs when no speaker labels are
    present (auto-generated captions frequently have none).
    """
    lines = text.split("\n")
    turns: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if _TRANSCRIPT_SPEAKER_RE.match(line) and current:
            turns.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        turns.append(current)

    units: list[_Unit] = []
    for turn in turns:
        joined = "\n".join(turn).strip()
        if not joined:
            continue
        stamp = _TIMESTAMP_RE.search(joined)
        units.append(_Unit(
            text=joined,
            heading_path=[],
            locator=stamp.group(1) if stamp else None,
        ))

    if len(units) <= 1:
        # No speaker structure found -- fall back to paragraphs so a caption
        # dump still chunks sensibly instead of becoming one giant unit.
        return _paragraph_units(text)
    return units


# ── Public API ───────────────────────────────────────────────────────────────


def chunk_document(
    source: str | list[Block],
    *,
    file_name: str = "",
    doc_kind: DocKind = "sow_document",
    document_title: str | None = None,
    max_chars: int | None = None,
) -> list[Chunk]:
    """Split a document into structure-aware chunks.

    `source` is either a block list from app.services.doc_blocks (preferred
    -- full structure available) or raw text (transcripts, and any caller
    that has not been migrated to blocks yet).

    `max_chars=None` (the default) resolves the budget from `doc_kind` via
    max_chars_for(); pass an explicit value to override it.

    Raises IngestError on empty input; never returns [] as a success.
    """
    if max_chars is None:
        max_chars = max_chars_for(doc_kind)
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    if isinstance(source, str):
        if not source.strip():
            raise IngestError("Cannot chunk an empty document.")
        if doc_kind == "transcript":
            units = _transcript_units(source)
            strategy = "speaker_turn"
        else:
            units = _paragraph_units(source)
            strategy = "paragraph"
    else:
        if not source:
            raise IngestError("Cannot chunk an empty document.")
        units = _units_from_blocks(source)
        strategy = _strategy_for(file_name, doc_kind, source)

    if not units:
        raise IngestError("Cannot chunk an empty document.")

    units, degraded = _normalise_units(units, max_chars)
    groups = _pack(units, max_chars)
    total = len(groups)
    total_chars = sum(u.size for u in units) + max(0, len(units) - 1) * 2

    chunks: list[Chunk] = []
    cursor = 0
    previous_text = ""

    for i, group in enumerate(groups, start=1):
        text = "\n\n".join(u.text for u in group)
        start_char = cursor
        end_char = cursor + len(text)
        cursor = end_char + 2

        heading_path = _group_heading_path(group)
        locator = _group_locator(group)
        group_degraded = degraded and any(not u.splittable for u in group)

        header = _render_context_header(
            document_title=document_title,
            heading_path=heading_path,
            locator=locator,
            index=i,
            total=total,
            start_char=start_char,
            end_char=end_char,
            total_chars=total_chars,
            preceding_tail=previous_text[-CONTEXT_TAIL_CHARS:] if previous_text else "",
        )

        chunks.append(Chunk(
            index=i,
            total=total,
            text=text,
            heading_path=heading_path,
            locator=locator,
            strategy=STRATEGY_HARD_SPLIT if (group_degraded and len(text) >= max_chars)
            else strategy,
            context_header=header,
        ))
        previous_text = text

    logger.info(
        "doc_chunking: %s split into %d chunk(s) via %s (%d degraded)",
        file_name or doc_kind, len(chunks), strategy,
        sum(1 for c in chunks if c.is_degraded),
    )
    return chunks
