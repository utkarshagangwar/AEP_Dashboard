"""SOW completeness audit (Phase 4 — "Pass 3", per SOW_FEATURE_PLAN.md §2).

This is deliberately a SEPARATE LLM call from the one that drafted the
section (plan §2 Pass 3: "self-grading is unreliable"). app.services.
sow_drafting.draft_section already has a cheap, mechanical safety net —
it checks whether every ui_element fact got a control_spec block carrying
its fact_index, and auto-appends a flagged callout for anything missing.
That check is structural: it can tell a control_spec is *present*, not
whether the drafted text actually describes that control's behavior
correctly. This module is the semantic check on top of it — an
independent reviewer re-reading the drafted prose against the original
facts and judging, fact by fact, whether it was actually represented, not
just referenced.

Scope: only the functional (grouped) sections produced by
sow_drafting.group_ledger_into_sections are audited. The Project
Overview/Scope of Work framing sections (app.services.sow_assembly) are
narrative summaries by design — "a table of contents for what follows,
not a restatement of every detail" — auditing them against a fact-by-fact
checklist would be measuring the wrong thing. The five templated trailing
sections (Out of Scope, Assumptions, etc.) are static placeholders with no
facts to audit against at all. All of these intentionally keep
coverage_score = NULL forever, not "not yet audited" — see
app.workers.tasks.sow_generation for where that boundary is enforced.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.design_ingest import IngestError

logger = get_logger(__name__)

_AUDIT_SYSTEM = (
    "You are an independent QA reviewer auditing a drafted Statement of "
    "Work section. You did NOT write this draft — your job is to catch "
    "anything the writer missed, glossed over, or got wrong, not to "
    "praise it. This audit produces a coverage score a QA engineer will "
    "use to decide whether this section is trustworthy enough to build "
    "automated test checkpoints from — err toward flagging anything even "
    "partially unclear or incomplete as NOT covered; a false 'covered' is "
    "far more costly here than a false 'missing' (a missing requirement "
    "silently skips a test; a wrongly-flagged one just gets a second "
    "look).\n\n"
    "You will be given a numbered list of facts and the section's drafted "
    "text. Respond with JSON only:\n"
    '{"results": [{"index": int, "covered": bool, "reason": str}, ...]}\n\n'
    "Include exactly one entry per fact given, using its index. covered=true "
    "only if the draft explicitly and correctly represents that specific "
    "fact — for a ui_element fact, its control type, label, AND behavior "
    "must all be present and accurate, not merely mentioned in passing; "
    "for a feature/decision/open_question fact, its substance must "
    "actually appear, not just be implied. reason: always give one short "
    "sentence — why it's covered, or specifically what's missing/wrong if "
    "it isn't."
)


def audit_section(heading: str, blocks: list[dict], facts: list) -> tuple[int, list[dict], str]:
    """facts: the ordered list of SowRequirementsLedger ORM rows assigned to
    this section (same list, same order, that was passed to
    sow_drafting.draft_section — indices must line up, since this reuses
    that same index-based tagging approach for the same
    never-cite-a-real-UUID reason).

    Returns (coverage_score 0-100, coverage_gaps, model_used) — model_used
    is returned for the same traceability reason draft_section/
    draft_overview return it (SowDocumentVersion.generated_by_model records
    every model that contributed to a version, audit included).
    coverage_gaps is a list of {fact_index, fact_type, element_type, label,
    reason} dicts — the stored, authoritative record of what this audit
    found missing, independent of (and a stronger signal than)
    sow_drafting's own structural safety-net callout.

    Raises IngestError only on total LLM failure — the caller (generate_
    sow_task) treats an audit failure as "leave coverage_score/gaps null,
    log a warning, keep the section" rather than failing a section that
    drafted successfully. An audit that can't run is a missing signal, not
    a drafting defect.
    """
    from app.services import llm_router
    from app.services.sow_drafting import _fact_summary, render_blocks_markdown

    if not facts:
        return 100, [], ""

    indexed = [_fact_summary(f, i) for i, f in enumerate(facts)]
    section_text = render_blocks_markdown(blocks)
    prompt = (
        f"Section: {heading}\n\n"
        f"Facts to verify:\n{indexed}\n\n"
        f"Drafted section text:\n{section_text}"
    )

    try:
        result = llm_router.complete(prompt, system=_AUDIT_SYSTEM, expect_json=True, max_tokens=4096)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed while auditing section '{heading}': {exc}") from exc

    raw = result.parsed_json or {}
    raw_results = raw.get("results", []) if isinstance(raw, dict) else []

    covered_indices: set[int] = set()
    addressed: set[int] = set()
    gaps: list[dict] = []

    for entry in raw_results:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(facts)) or idx in addressed:
            continue
        addressed.add(idx)
        reason = str(entry.get("reason") or "").strip()[:1000]
        if entry.get("covered") is True:
            covered_indices.add(idx)
        else:
            gaps.append(_gap_entry(facts[idx], idx, reason or "Flagged as not covered by the audit."))

    # Any fact index the audit response never addressed at all is ALSO a
    # gap, never assumed covered by omission — same principle used
    # throughout this feature (group_ledger_into_sections' auto-recovery,
    # draft_section's own completeness net).
    for i, fact in enumerate(facts):
        if i not in addressed:
            gaps.append(_gap_entry(fact, i, "Audit response did not address this fact."))

    score = round(100 * len(covered_indices) / len(facts))
    logger.info(
        "SOW audit: section '%s' — %d/%d fact(s) covered (%d%%), %d gap(s), via %s",
        heading, len(covered_indices), len(facts), score, len(gaps), result.model_used,
    )
    return score, gaps, result.model_used


def _gap_entry(fact, index: int, reason: str) -> dict:
    return {
        "fact_index": index,
        "fact_type": fact.fact_type.value if hasattr(fact.fact_type, "value") else str(fact.fact_type),
        "element_type": (
            fact.element_type.value if fact.element_type and hasattr(fact.element_type, "value") else fact.element_type
        ),
        "label": fact.label,
        "reason": reason,
    }
