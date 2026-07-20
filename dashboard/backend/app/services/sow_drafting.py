"""SOW section drafting (Phase 3 — "Pass 2", per SOW_FEATURE_PLAN.md §2).

Turns the flat requirements ledger (app.models.sow.SowRequirementsLedger,
produced by app.services.sow_ledger in Phase 1) into structured SOW
sections: first grouped by feature/page/module, then each drafted
independently into typed content_blocks (§11.6 schema).

Two LLM passes, each with its own safety net so a model mistake degrades
gracefully instead of silently losing a requirement:

  group_ledger_into_sections — asks the model to group facts by INDEX, not
  by UUID (an LLM copying full UUIDs verbatim is a real hallucination risk;
  small integers are not). Any fact index the model's grouping misses is
  swept into an auto-generated "Additional Items" section rather than
  silently dropped.

  draft_section — asks the model to write one section's content_blocks
  from its assigned facts, with an explicit requirement that every
  ui_element fact gets exactly one control_spec block carrying a
  fact_index back to the source ledger row. After parsing, any ui_element
  fact the model's draft never referenced gets an auto-appended callout
  block flagging it — a lightweight completeness safety net built into
  drafting itself, ahead of the full completeness audit pass (Phase 4).

Neither pass is the formal completeness AUDIT the plan describes for
Phase 4 (a separate, independently-run verification pass) — these are
guardrails inside generation, not a substitute for auditing it afterward.
"""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.services.design_ingest import IngestError

logger = get_logger(__name__)

_VALID_ELEMENT_TYPES = {
    "button", "dropdown", "filter", "checkbox", "toggle", "slider",
    "three_dot_menu", "tab", "modal", "other",
}
_MAX_SECTION_FACTS = 60  # a section this large should have been split by the
                          # grouping pass — sanity ceiling only
_MAX_HEADING_CHARS = 200
_MAX_BLOCKS_PER_SECTION = 100


def _slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "section")[:max_len]


def _unique_key(base: str, used: set[str]) -> str:
    key = base
    n = 2
    while key in used:
        key = f"{base}-{n}"
        n += 1
    used.add(key)
    return key


def _fact_summary(fact, index: int) -> dict:
    """Compact, index-tagged representation of one ledger fact for prompts —
    never includes the fact's real UUID (see module docstring)."""
    return {
        "index": index,
        "fact_type": fact.fact_type.value if hasattr(fact.fact_type, "value") else str(fact.fact_type),
        "element_type": (
            fact.element_type.value if fact.element_type and hasattr(fact.element_type, "value") else fact.element_type
        ),
        "label": fact.label,
        "location": fact.location,
        "behavior_notes": fact.behavior_notes,
    }


# ── Pass 2a: grouping ────────────────────────────────────────────────────────

_GROUPING_SYSTEM = (
    "You are a senior technical writer organizing QA requirements facts "
    "into logical sections for a Statement of Work. You will be given a "
    "numbered (indexed) list of facts extracted from meeting transcripts, "
    "meeting recordings, and design references. Group them into sections "
    "by feature/page/module. Respond with JSON only:\n"
    '{"sections": [{"heading": str, "fact_indices": [int, ...]}]}\n\n'
    "Rules:\n"
    "- EVERY fact index from the input must appear in exactly one section's "
    "fact_indices — no fact left ungrouped, no fact duplicated across "
    "sections. This is the single most important rule: a missed index is a "
    "silently-lost requirement.\n"
    "- Group by what the facts are ABOUT (e.g. every fact about a login "
    "screen goes together), not by fact_type — a section naturally mixes "
    "feature/decision/ui_element/open_question facts about the same area.\n"
    "- heading: a short (3-8 word) title, e.g. \"User Login\", \"Skills "
    "Table — Bulk Actions\".\n"
    "- Prefer more, smaller, focused sections over one giant catch-all — "
    "except for facts that are genuinely miscellaneous/unrelated to "
    "everything else, which can share one \"General\" section."
)


def group_ledger_into_sections(facts: list) -> tuple[list[dict], str]:
    """facts: ordered list of SowRequirementsLedger ORM rows (already
    filtered to superseded=False by the caller). Returns
    ([{heading, section_key, fact_indices}], model_used). Every input index
    is guaranteed to appear in exactly one returned group's fact_indices —
    see module docstring for the auto-recovery guarantee.

    Raises IngestError only if the LLM call itself fails outright (every
    fact still gets grouped in that case too — see the caller's fallback,
    which treats total failure as "one big ungrouped section" rather than
    failing the whole generation over a grouping-pass hiccup).
    """
    from app.services import llm_router

    if not facts:
        return [], ""

    indexed = [_fact_summary(f, i) for i, f in enumerate(facts)]
    prompt = "Group these requirement facts into SOW sections:\n\n" + str(indexed)

    try:
        result = llm_router.complete(prompt, system=_GROUPING_SYSTEM, expect_json=True, max_tokens=8192)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed during section grouping: {exc}") from exc

    raw = result.parsed_json or {}
    raw_sections = raw.get("sections", []) if isinstance(raw, dict) else []

    claimed: set[int] = set()
    used_keys: set[str] = set()
    groups: list[dict] = []
    for entry in raw_sections:
        if not isinstance(entry, dict):
            continue
        heading = str(entry.get("heading") or "").strip()[:_MAX_HEADING_CHARS]
        if not heading:
            continue
        raw_indices = entry.get("fact_indices")
        if not isinstance(raw_indices, list):
            continue
        # First-claim wins: an index the model assigned to two sections is
        # kept only in the first, never silently duplicated into both.
        indices = [
            i for i in raw_indices
            if isinstance(i, int) and 0 <= i < len(facts) and i not in claimed
        ]
        if not indices:
            continue
        claimed.update(indices)
        groups.append({
            "heading": heading,
            "section_key": _unique_key(_slugify(heading), used_keys),
            "fact_indices": indices,
        })

    # Auto-recovery: any index the grouping pass missed entirely (model
    # error, malformed response, or genuinely didn't fit anywhere) still
    # gets a section — never silently dropped from the document.
    missed = [i for i in range(len(facts)) if i not in claimed]
    if missed:
        logger.warning(
            "SOW drafting: grouping pass missed %d/%d fact(s), auto-recovering into "
            "an 'Additional Items' section",
            len(missed), len(facts),
        )
        groups.append({
            "heading": "Additional Items",
            "section_key": _unique_key("additional-items", used_keys),
            "fact_indices": missed,
        })

    logger.info(
        "SOW drafting: grouped %d fact(s) into %d section(s) via %s",
        len(facts), len(groups), result.model_used,
    )
    return groups, result.model_used


# ── Pass 2b: per-section drafting ────────────────────────────────────────────

_DRAFT_RESPONSE_SHAPE = (
    '{"blocks": [\n'
    '  {"type": "heading", "level": int, "text": str},\n'
    '  {"type": "paragraph", "text": str},\n'
    '  {"type": "control_spec", "element_type": "button"|"dropdown"|'
    '"filter"|"checkbox"|"toggle"|"slider"|"three_dot_menu"|"tab"|"modal"|'
    '"other", "label": str, "behavior": str, "fact_index": int},\n'
    '  {"type": "bullet_list", "items": [str, ...]},\n'
    '  {"type": "table", "headers": [str, ...], "rows": [[str, ...], ...]},\n'
    '  {"type": "callout", "tone": "info"|"warning", "text": str}\n'
    "]}"
)

_DRAFT_SYSTEM = (
    "You are a senior technical writer producing one section of an "
    "exhaustive, functionality-first Statement of Work. This document will "
    "later drive AI-generated QA test checkpoints ('vibe testing') — "
    "anything you fail to mention will be MISSING from testing, which has "
    "real business impact, so completeness matters more than brevity or "
    "elegant prose. Respond with JSON only:\n"
    f"{_DRAFT_RESPONSE_SHAPE}\n\n"
    "Rules:\n"
    "- Start with one 'heading' block (level 2) matching the section title "
    "given to you.\n"
    "- Add 1-3 'paragraph' blocks giving context/purpose, synthesizing the "
    "'feature'/'decision' facts you were given.\n"
    "- For EVERY fact of type 'ui_element' in your input, emit EXACTLY ONE "
    "'control_spec' block: element_type = that fact's element_type, label = "
    "its label, behavior = a clear, testable description of what the "
    "control does (combine the fact's location/behavior_notes into "
    "something a QA tester could act on), fact_index = that input fact's "
    "index (required — this is how the control traces back to its "
    "source; never omit it).\n"
    "- For every fact of type 'open_question', emit one 'callout' block "
    "(tone 'warning') summarizing the open question.\n"
    "- Do NOT invent controls, behavior, or requirements not present in "
    "your input facts — every claim must trace back to a specific input "
    "fact. Do not skip, merge, or summarize away any ui_element fact — if "
    "two facts genuinely describe the exact same control, you may combine "
    "them into one control_spec, but completeness always wins over "
    "conciseness when in doubt."
)


def _validate_block(item: object, valid_fact_indices: set[int]) -> dict | None:
    if not isinstance(item, dict):
        return None
    btype = item.get("type")

    if btype == "heading":
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        level = item.get("level")
        level = level if isinstance(level, int) and 1 <= level <= 4 else 2
        return {"type": "heading", "level": level, "text": text[:300]}

    if btype == "paragraph":
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        return {"type": "paragraph", "text": text[:5000]}

    if btype == "control_spec":
        label = str(item.get("label") or "").strip()
        if not label:
            return None
        element_type = item.get("element_type")
        if element_type not in _VALID_ELEMENT_TYPES:
            element_type = "other"
        behavior = str(item.get("behavior") or "").strip()[:2000] or None
        fact_index = item.get("fact_index")
        fact_index = fact_index if isinstance(fact_index, int) and fact_index in valid_fact_indices else None
        return {
            "type": "control_spec",
            "element_type": element_type,
            "label": label[:500],
            "behavior": behavior,
            "fact_index": fact_index,
        }

    if btype == "bullet_list":
        items = item.get("items")
        if not isinstance(items, list):
            return None
        cleaned = [str(i).strip()[:500] for i in items if str(i).strip()][:50]
        if not cleaned:
            return None
        return {"type": "bullet_list", "items": cleaned}

    if btype == "table":
        headers = item.get("headers")
        rows = item.get("rows")
        if not isinstance(headers, list) or not isinstance(rows, list):
            return None
        headers = [str(h).strip()[:200] for h in headers][:20]
        cleaned_rows = []
        for row in rows[:100]:
            if isinstance(row, list):
                cleaned_rows.append([str(c).strip()[:500] for c in row][:20])
        if not headers or not cleaned_rows:
            return None
        return {"type": "table", "headers": headers, "rows": cleaned_rows}

    if btype == "callout":
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        tone = item.get("tone")
        tone = tone if tone in ("info", "warning") else "info"
        return {"type": "callout", "tone": tone, "text": text[:2000]}

    return None


def draft_section(heading: str, facts: list) -> tuple[list[dict], str]:
    """facts: ordered list of SowRequirementsLedger ORM rows assigned to
    this section. Returns (content_blocks, model_used) — content_blocks is
    already schema-validated AND completeness-checked against the input
    ui_element facts (see module docstring). Raises IngestError only on
    total LLM failure."""
    from app.services import llm_router

    indexed = [_fact_summary(f, i) for i, f in enumerate(facts[:_MAX_SECTION_FACTS])]
    prompt = f"Section heading: {heading}\n\nAssigned facts:\n{indexed}"

    try:
        result = llm_router.complete(prompt, system=_DRAFT_SYSTEM, expect_json=True, max_tokens=8192)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed while drafting section '{heading}': {exc}") from exc

    raw = result.parsed_json or {}
    raw_blocks = raw.get("blocks", []) if isinstance(raw, dict) else []
    valid_indices = set(range(len(indexed)))
    blocks = [b for b in (_validate_block(i, valid_indices) for i in raw_blocks) if b]
    blocks = blocks[:_MAX_BLOCKS_PER_SECTION]

    dropped = len(raw_blocks) - len(blocks)
    if dropped:
        logger.warning(
            "SOW drafting: section '%s' — dropped %d schema-invalid block(s)", heading, dropped
        )

    if not blocks or blocks[0].get("type") != "heading":
        blocks.insert(0, {"type": "heading", "level": 2, "text": heading[:300]})

    # Completeness safety net: any ui_element fact this draft never
    # referenced via a control_spec's fact_index gets auto-appended as a
    # flagged callout — never silently missing, even before the formal
    # audit pass (Phase 4) exists.
    referenced = {
        b["fact_index"] for b in blocks if b.get("type") == "control_spec" and b.get("fact_index") is not None
    }
    gaps = []
    for i, fact in enumerate(facts[:_MAX_SECTION_FACTS]):
        if i in referenced:
            continue
        ftype = fact.fact_type.value if hasattr(fact.fact_type, "value") else str(fact.fact_type)
        if ftype != "ui_element":
            continue
        etype = fact.element_type.value if fact.element_type and hasattr(fact.element_type, "value") else fact.element_type
        gaps.append(f"{fact.label} ({etype or 'control'})" + (f" — {fact.location}" if fact.location else ""))

    if gaps:
        logger.warning(
            "SOW drafting: section '%s' — %d ui_element fact(s) not referenced by the "
            "draft, auto-appending as flagged items", heading, len(gaps),
        )
        blocks.append({
            "type": "callout",
            "tone": "warning",
            "text": (
                "Additional elements (auto-recovered — not explicitly covered in the "
                "drafted text above, added from the requirements ledger; verify "
                "manually): " + "; ".join(gaps)
            ),
        })

    logger.info(
        "SOW drafting: section '%s' drafted (%d block(s), %d ui_element gap(s) recovered) via %s",
        heading, len(blocks), len(gaps), result.model_used,
    )
    return blocks, result.model_used


# ── Markdown rendering (read-only display; §11.7 — rendered on demand, ─────
# never stored, so it can never go stale relative to content_blocks) ────────

def render_blocks_markdown(blocks: list[dict]) -> str:
    lines: list[str] = []
    for b in blocks or []:
        btype = b.get("type")
        if btype == "heading":
            level = b.get("level", 2)
            lines.append(f"{'#' * max(1, min(level, 6))} {b.get('text', '')}")
        elif btype == "paragraph":
            lines.append(b.get("text", ""))
        elif btype == "control_spec":
            label = b.get("label", "")
            element_type = (b.get("element_type") or "control").replace("_", " ")
            behavior = b.get("behavior") or ""
            lines.append(f"- **{label}** ({element_type}){': ' + behavior if behavior else ''}")
        elif btype == "bullet_list":
            for item in b.get("items", []):
                lines.append(f"- {item}")
        elif btype == "table":
            headers = b.get("headers", [])
            rows = b.get("rows", [])
            if headers:
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("|" + "|".join(["---"] * len(headers)) + "|")
                for row in rows:
                    lines.append("| " + " | ".join(row) + " |")
        elif btype == "callout":
            tone = "⚠️" if b.get("tone") == "warning" else "ℹ️"
            lines.append(f"> {tone} {b.get('text', '')}")
        lines.append("")  # blank line between blocks
    return "\n".join(lines).strip()
