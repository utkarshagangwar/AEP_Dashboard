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
# How many facts go into ONE drafting call. A section with more than this is
# drafted in successive passes and the blocks concatenated -- it is NOT
# truncated. The previous constant (_MAX_SECTION_FACTS = 60) sliced the fact
# list with `facts[:60]`, so every fact past the 60th vanished from the
# drafted section silently: no warning, and the auto-recovery callout below
# only ever saw the surviving 60, so it could not flag them either. On a
# large imported SOW that was the single largest source of missing content in
# the generated document.
_FACTS_PER_DRAFT_CALL = 45
# Facts per grouping call. Grouping used to send the ENTIRE ledger in one
# request; with several hundred facts the response (every heading plus every
# index) overran the token budget, got repaired into a valid-but-short list,
# and the "Additional Items" net below then swept hundreds of unclaimed facts
# into a single catch-all section.
_FACTS_PER_GROUPING_CALL = 80
_MAX_HEADING_CHARS = 200
_BLOCKS_PER_DRAFT_CALL = 100
# Fraction of facts that may end up in "Additional Items" before the grouping
# pass is considered to have malfunctioned rather than just been imperfect.
_GROUPING_MISS_ALARM_RATIO = 0.1


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


_HEADING_MERGE_SYSTEM = (
    "You are consolidating draft section headings for a single Statement of "
    "Work. The same feature area was described in several separate passes, so "
    "the list below may contain near-duplicate headings for the same thing "
    "(e.g. \"Skills Table Filters\" and \"Skills Table — Filtering\"). Map each "
    "heading onto a canonical heading. Respond with JSON only:\n"
    '{"mapping": [{"from": str, "to": str}]}\n\n'
    "Rules:\n"
    "- Every input heading must appear exactly once as a 'from'.\n"
    "- Headings that describe the SAME feature area share a 'to'; use the "
    "clearest of them as the canonical form.\n"
    "- A heading with no near-duplicate maps to ITSELF.\n"
    "- Do not invent headings that aren't in the input, and do not merge "
    "genuinely different feature areas just to shorten the list."
)


def _outline_key(fact) -> tuple[str, ...] | None:
    """The source document heading path this fact came from, if any."""
    path = getattr(fact, "source_heading_path", None)
    if isinstance(path, list) and path:
        return tuple(str(p) for p in path if str(p).strip())
    return None


def _group_by_source_outline(facts: list) -> list[dict] | None:
    """Group facts by the heading they occupied in the imported document.

    Returns None when too few facts carry an outline for this to be
    meaningful (transcripts, recordings and design images have no document
    structure at all), in which case the caller falls back to the LLM pass.

    This exists because an imported SOW already HAS an organisation, chosen
    by whoever wrote it, and asking a model to invent a fresh one discards
    that — the regenerated document comes back reordered and renamed, which
    reads as "it lost my content" even when every fact survived. Grouping on
    the chunker's own structural record reproduces the source's section order
    exactly, cannot hallucinate, and costs no tokens.
    """
    if not facts:
        return None

    keyed = [_outline_key(f) for f in facts]
    covered = sum(1 for k in keyed if k)
    if covered < len(facts) * 0.8:
        return None

    used_keys: set[str] = set()
    by_path: dict[tuple[str, ...], dict] = {}
    unstructured: list[int] = []

    for index, key in enumerate(keyed):
        if key is None:
            unstructured.append(index)
            continue
        group = by_path.get(key)
        if group is None:
            heading = key[-1][:_MAX_HEADING_CHARS]
            group = {
                "heading": heading,
                "section_key": _unique_key(_slugify(heading), used_keys),
                "fact_indices": [],
            }
            by_path[key] = group
        group["fact_indices"].append(index)

    groups = list(by_path.values())  # dicts preserve first-seen (document) order
    if unstructured:
        groups.append({
            "heading": "Additional Items",
            "section_key": _unique_key("additional-items", used_keys),
            "fact_indices": unstructured,
        })

    logger.info(
        "SOW drafting: grouped %d fact(s) into %d section(s) from the source "
        "document's own outline (no LLM call)",
        len(facts), len(groups),
    )
    return groups


def _group_batch(facts: list, offset: int) -> list[dict]:
    """One grouping call over a slice of the ledger. Returns raw
    [{heading, fact_indices}] with GLOBAL indices; validation and
    conflict resolution happen in the caller."""
    from app.services import llm_router

    indexed = [_fact_summary(f, offset + i) for i, f in enumerate(facts)]
    prompt = "Group these requirement facts into SOW sections:\n\n" + str(indexed)

    result = llm_router.complete_json_complete(
        prompt, system=_GROUPING_SYSTEM, max_tokens=8192
    )
    raw = result.parsed_json or {}
    entries = raw.get("sections", []) if isinstance(raw, dict) else []
    return [
        {"heading": e.get("heading"), "fact_indices": e.get("fact_indices"), "model": result.model_used}
        for e in entries
        if isinstance(e, dict)
    ]


def _canonicalize_headings(headings: list[str]) -> dict[str, str]:
    """Map near-duplicate headings from different batches onto one form.

    Falls back to identity (exact-string merge only) on any failure. Nothing
    is at risk in that case — two batches keep two similar sections, which is
    cosmetic, not a lost requirement — so this never raises.
    """
    from app.services import llm_router

    unique = sorted({h for h in headings if h})
    if len(unique) < 2:
        return {h: h for h in unique}

    try:
        result = llm_router.complete_json_complete(
            "Consolidate these headings:\n\n" + "\n".join(unique),
            system=_HEADING_MERGE_SYSTEM,
            max_tokens=4096,
        )
    except Exception:  # noqa: BLE001 — cosmetic pass, never fatal
        logger.warning("SOW drafting: heading consolidation failed, keeping headings as-is", exc_info=True)
        return {h: h for h in unique}

    raw = result.parsed_json or {}
    entries = raw.get("mapping", []) if isinstance(raw, dict) else []
    mapping = {h: h for h in unique}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("from") or "").strip()
        dst = str(entry.get("to") or "").strip()[:_MAX_HEADING_CHARS]
        # Only remap headings we actually sent; a canonical form the model
        # invented wholesale is not allowed to replace a real one.
        if src in mapping and dst:
            mapping[src] = dst
    return mapping


def group_ledger_into_sections(facts: list) -> tuple[list[dict], str]:
    """facts: ordered list of SowRequirementsLedger ORM rows (already
    filtered to superseded=False by the caller). Returns
    ([{heading, section_key, fact_indices}], model_used). Every input index
    is guaranteed to appear in exactly one returned group's fact_indices —
    see module docstring for the auto-recovery guarantee.

    Two strategies, in order:

    1. If most facts carry a `source_heading_path` (they came from an
       imported document), group deterministically on it — the regenerated
       SOW then mirrors the source's own outline and order. See
       _group_by_source_outline.
    2. Otherwise run the LLM grouping pass in batches of
       _FACTS_PER_GROUPING_CALL and consolidate near-duplicate headings
       across batches. Batching is what keeps the response inside the token
       budget: a single call over a few hundred facts overran it, was
       repaired into a short list, and dumped everything unclaimed into one
       catch-all section.

    Raises IngestError only if every LLM call fails outright (every fact
    still gets grouped in that case too — see the caller's fallback, which
    treats total failure as "one big ungrouped section" rather than failing
    the whole generation over a grouping-pass hiccup).
    """
    from app.services import llm_router

    if not facts:
        return [], ""

    from_outline = _group_by_source_outline(facts)
    if from_outline is not None:
        return from_outline, "source-outline (deterministic)"

    # ── Map: group each batch independently ──────────────────────────────
    raw_groups: list[dict] = []
    models_used: list[str] = []
    failures = 0
    total_batches = 0

    for offset in range(0, len(facts), _FACTS_PER_GROUPING_CALL):
        total_batches += 1
        batch = facts[offset : offset + _FACTS_PER_GROUPING_CALL]
        try:
            entries = _group_batch(batch, offset)
        except llm_router.LLMRouterError as exc:
            failures += 1
            logger.warning(
                "SOW drafting: grouping batch at offset %d failed (%s) — its facts "
                "will be auto-recovered below", offset, exc,
            )
            continue
        for entry in entries:
            model = entry.pop("model", None)
            if model and model not in models_used:
                models_used.append(model)
            raw_groups.append(entry)

    if failures == total_batches:
        raise IngestError(
            f"All {total_batches} section-grouping call(s) failed — cannot organize "
            "the ledger into sections."
        )

    # ── Reduce: collapse near-duplicate headings across batches ──────────
    headings = [
        str(g.get("heading") or "").strip()[:_MAX_HEADING_CHARS] for g in raw_groups
    ]
    canonical = _canonicalize_headings([h for h in headings if h])

    claimed: set[int] = set()
    used_keys: set[str] = set()
    by_heading: dict[str, dict] = {}
    groups: list[dict] = []

    for entry, heading in zip(raw_groups, headings):
        if not heading:
            continue
        heading = canonical.get(heading, heading)
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

        existing = by_heading.get(heading)
        if existing is not None:
            existing["fact_indices"].extend(indices)
            continue
        group = {
            "heading": heading,
            "section_key": _unique_key(_slugify(heading), used_keys),
            "fact_indices": indices,
        }
        by_heading[heading] = group
        groups.append(group)

    # Auto-recovery: any index the grouping pass missed entirely (model
    # error, malformed response, failed batch, or genuinely didn't fit
    # anywhere) still gets a section — never silently dropped.
    missed = [i for i in range(len(facts)) if i not in claimed]
    if missed:
        log = (
            logger.error
            if len(missed) > len(facts) * _GROUPING_MISS_ALARM_RATIO
            else logger.warning
        )
        log(
            "SOW drafting: grouping pass missed %d/%d fact(s), auto-recovering into "
            "an 'Additional Items' section (%d/%d batch(es) failed)",
            len(missed), len(facts), failures, total_batches,
        )
        groups.append({
            "heading": "Additional Items",
            "section_key": _unique_key("additional-items", used_keys),
            "fact_indices": missed,
        })

    # Keep each section's facts in ledger order after cross-batch merging.
    for group in groups:
        group["fact_indices"].sort()

    model_label = ", ".join(models_used)
    logger.info(
        "SOW drafting: grouped %d fact(s) into %d section(s) across %d batch(es) via %s",
        len(facts), len(groups), total_batches, model_label or "unknown",
    )
    return groups, model_label


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


def _draft_pass(
    heading: str, facts: list, offset: int, pass_no: int, total_passes: int
) -> tuple[list[dict], str]:
    """One drafting call over a window of a section's facts.

    `offset` is the window's start in the section's full fact list, so
    every returned control_spec's fact_index is a SECTION-global index —
    the completeness check in draft_section depends on that.
    """
    from app.services import llm_router

    indexed = [_fact_summary(f, offset + i) for i, f in enumerate(facts)]
    prompt = f"Section heading: {heading}\n\nAssigned facts:\n{indexed}"
    system = _DRAFT_SYSTEM
    if total_passes > 1:
        system += (
            f"\n\nThis is continuation {pass_no} of {total_passes} for this same "
            "section — the facts above are one batch of a larger set. Emit the "
            "level-2 heading block ONLY if this is continuation 1; otherwise "
            "start directly with the content. Do not write a summary or "
            "conclusion, and do not restate facts from other continuations: "
            "your blocks are concatenated with the others in order."
        )

    result = llm_router.complete_json_complete(prompt, system=system, max_tokens=8192)
    raw = result.parsed_json or {}
    raw_blocks = raw.get("blocks", []) if isinstance(raw, dict) else []
    valid_indices = set(range(offset, offset + len(facts)))
    blocks = [b for b in (_validate_block(i, valid_indices) for i in raw_blocks) if b]
    blocks = blocks[:_BLOCKS_PER_DRAFT_CALL]

    dropped = len(raw_blocks) - len(blocks)
    if dropped:
        logger.warning(
            "SOW drafting: section '%s' pass %d — dropped %d schema-invalid block(s)",
            heading, pass_no, dropped,
        )
    return blocks, result.model_used


def draft_section(heading: str, facts: list) -> tuple[list[dict], str]:
    """facts: ordered list of SowRequirementsLedger ORM rows assigned to
    this section. Returns (content_blocks, model_used) — content_blocks is
    already schema-validated AND completeness-checked against the input
    ui_element facts (see module docstring). Raises IngestError only on
    total LLM failure.

    A section with more facts than fit in one call is drafted in successive
    passes and the blocks concatenated. It is never truncated: the previous
    `facts[:60]` slice dropped every later fact from both the draft AND the
    completeness net below, so a large section came back looking complete
    while most of its requirements were simply gone.
    """
    from app.services import llm_router

    total = len(facts)
    windows = [
        facts[i : i + _FACTS_PER_DRAFT_CALL]
        for i in range(0, max(total, 1), _FACTS_PER_DRAFT_CALL)
    ] or [[]]
    total_passes = len(windows)
    if total_passes > 1:
        logger.info(
            "SOW drafting: section '%s' has %d fact(s) — drafting in %d passes",
            heading, total, total_passes,
        )

    blocks: list[dict] = []
    models_used: list[str] = []
    failures: list[str] = []

    for pass_no, window in enumerate(windows, start=1):
        offset = (pass_no - 1) * _FACTS_PER_DRAFT_CALL
        try:
            pass_blocks, model_used = _draft_pass(
                heading, window, offset, pass_no, total_passes
            )
        except llm_router.LLMRouterError as exc:
            failures.append(f"pass {pass_no}: {exc}")
            logger.warning(
                "SOW drafting: section '%s' pass %d/%d failed: %s",
                heading, pass_no, total_passes, exc,
            )
            continue
        if pass_no > 1:
            # Only the first pass owns the section heading; a continuation
            # that emits one anyway would put a duplicate H2 mid-section.
            pass_blocks = [b for b in pass_blocks if b.get("type") != "heading"]
        blocks.extend(pass_blocks)
        if model_used not in models_used:
            models_used.append(model_used)

    if failures and len(failures) == total_passes:
        raise IngestError(
            f"All LLM providers failed while drafting section '{heading}': "
            + "; ".join(failures)
        )

    if not blocks or blocks[0].get("type") != "heading":
        blocks.insert(0, {"type": "heading", "level": 2, "text": heading[:300]})

    # Completeness safety net: any ui_element fact this draft never
    # referenced via a control_spec's fact_index gets auto-appended as a
    # flagged callout — never silently missing, even before the formal
    # audit pass (Phase 4) exists. Runs over the FULL fact list, including
    # facts from a pass that failed above.
    referenced = {
        b["fact_index"] for b in blocks if b.get("type") == "control_spec" and b.get("fact_index") is not None
    }
    gaps = []
    for i, fact in enumerate(facts):
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

    if failures:
        # A partially drafted section must say so in the document itself, not
        # only in the logs — otherwise it reads as complete.
        blocks.append({
            "type": "callout",
            "tone": "warning",
            "text": (
                f"{len(failures)} of {total_passes} drafting pass(es) for this section "
                "failed; some requirements above may be described only in the "
                "auto-recovered list. Re-run Rewrite for this section to retry."
            ),
        })

    model_label = ", ".join(models_used)
    logger.info(
        "SOW drafting: section '%s' drafted (%d fact(s), %d pass(es), %d block(s), "
        "%d ui_element gap(s) recovered) via %s",
        heading, total, total_passes, len(blocks), len(gaps), model_label or "unknown",
    )
    return blocks, model_label


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
