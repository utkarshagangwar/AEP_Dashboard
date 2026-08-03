"""Which already-drafted sections does a newly attached source affect?

Attaching a meeting transcript, recording, design reference or second SOW
document to a document that has ALREADY been generated poses a question the
rest of the pipeline had no answer for: the new source's facts are in the
ledger, but nothing connects them to the sections that exist. The user's
only options were to regenerate the whole document (throwing away every
hand edit and re-paying for every section) or to guess which sections to
rewrite from a checkbox list.

This module answers the question. It assigns each new fact to an existing
section — by the source document's own heading structure when available,
otherwise with one batched LLM call against the existing section headings —
and returns the affected section keys.

Deliberately advisory. Nothing here redrafts anything: it records the
affected keys so the UI can pre-tick them in the Rewrite dialog, and the
user presses the button. Automatic redrafting on every upload would spend
drafting tokens without being asked and could overwrite a section the user
was in the middle of reviewing.

Two things it DOES write:
  * assigned_section_key on the new facts — same stamp
    generate_sow_task's Pass 2a applies, which is what makes the subsequent
    patch able to find "the facts belonging to this section".
  * superseded=True on older facts the new source contradicts/replaces.
    The column has existed since Phase 0 and is read everywhere, but
    nothing ever wrote True to it; without that, a re-stated requirement
    accumulated as a duplicate instead of replacing its predecessor.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.design_ingest import IngestError

logger = get_logger(__name__)

# Facts per assignment call — matches sow_drafting._FACTS_PER_GROUPING_CALL
# for the same reason: the response carries one entry per fact.
_FACTS_PER_ASSIGN_CALL = 80

_NEW_SECTION = "__new__"

_ASSIGN_SYSTEM = (
    "You are maintaining an existing Statement of Work. New requirement "
    "facts have arrived from a new source (a meeting, a design reference, or "
    "another document). Your ONLY job is to say which existing section each "
    "new fact belongs to. Respond with JSON only:\n"
    '{"assignments": [{"index": int, "section_key": str}]}\n\n'
    "Rules:\n"
    "- 'section_key' must be one of the section keys listed below, EXACTLY "
    f'as given, or the literal "{_NEW_SECTION}" if the fact is about a '
    "feature area none of the existing sections covers.\n"
    "- Include exactly one entry per fact, using the fact's index.\n"
    "- Choose by subject matter: a fact about the login screen belongs to "
    "the login section regardless of which source it came from.\n"
    f'- Prefer an existing section. Use "{_NEW_SECTION}" only when no '
    "existing section is a reasonable home — every new section means the "
    "user has to review a brand-new part of their document."
)


def _fact_label(fact) -> str:
    parts = [fact.label]
    if fact.location:
        parts.append(f"({fact.location})")
    if fact.behavior_notes:
        parts.append(f"— {fact.behavior_notes[:200]}")
    return " ".join(parts)


def _assign_by_outline(facts: list, sections_by_key: dict) -> dict[int, str] | None:
    """Assign facts using the source document's own heading structure.

    Only usable when the new source is an imported document AND its heading
    slugs line up with existing section keys — which is exactly the case
    when the existing SOW was itself generated from an imported document
    (sow_drafting._group_by_source_outline slugifies the same headings the
    same way). Returns None when it cannot cover most facts, so the caller
    falls back to the LLM pass. Costs nothing and cannot hallucinate.
    """
    from app.services.sow_drafting import _slugify

    assignments: dict[int, str] = {}
    for i, fact in enumerate(facts):
        path = getattr(fact, "source_heading_path", None)
        if not isinstance(path, list) or not path:
            continue
        key = _slugify(str(path[-1]))
        if key in sections_by_key:
            assignments[i] = key

    if len(assignments) < len(facts) * 0.8:
        return None
    logger.info(
        "SOW impact: matched %d/%d new fact(s) to existing sections by document "
        "outline (no LLM call)", len(assignments), len(facts),
    )
    return assignments


def _assign_by_llm(facts: list, sections_by_key: dict) -> dict[int, str]:
    """One batched call per _FACTS_PER_ASSIGN_CALL facts. A fact the model
    never addressed, or addressed with an unknown key, is left unassigned —
    the caller treats that as "needs a new section", which is the safe
    direction: it surfaces the fact for review rather than filing it
    somewhere wrong."""
    from app.services import llm_router

    section_list = "\n".join(
        f"- {key}: {heading}" for key, heading in sections_by_key.items()
    )
    assignments: dict[int, str] = {}
    failures = 0
    batches = 0

    for offset in range(0, len(facts), _FACTS_PER_ASSIGN_CALL):
        batches += 1
        batch = facts[offset : offset + _FACTS_PER_ASSIGN_CALL]
        listed = "\n".join(
            f"{offset + i}. [{f.fact_type.value if hasattr(f.fact_type, 'value') else f.fact_type}] "
            f"{_fact_label(f)}"
            for i, f in enumerate(batch)
        )
        prompt = (
            f"Existing sections:\n{section_list}\n\nNew facts to assign:\n{listed}"
        )
        try:
            result = llm_router.complete_json_complete(
                prompt, system=_ASSIGN_SYSTEM, max_tokens=8192
            )
        except llm_router.LLMRouterError as exc:
            failures += 1
            logger.warning("SOW impact: assignment batch at offset %d failed: %s", offset, exc)
            continue

        raw = result.parsed_json or {}
        entries = raw.get("assignments", []) if isinstance(raw, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            key = str(entry.get("section_key") or "").strip()
            if not isinstance(idx, int) or not (0 <= idx < len(facts)):
                continue
            if key in sections_by_key:
                assignments[idx] = key

    if failures and failures == batches:
        raise IngestError(
            f"All {batches} section-assignment call(s) failed for the new source."
        )
    return assignments


def assign_new_facts_to_sections(new_facts: list, sections_by_key: dict) -> dict[int, str]:
    """Map each new fact's index onto an existing section_key.

    Facts with no entry in the returned dict belong to no existing section —
    the caller surfaces those separately rather than forcing them into one.
    """
    if not new_facts or not sections_by_key:
        return {}

    by_outline = _assign_by_outline(new_facts, sections_by_key)
    if by_outline is not None:
        return by_outline
    return _assign_by_llm(new_facts, sections_by_key)


def mark_superseded(session, document_id, new_facts: list) -> int:
    """Retire older facts that the new source restates.

    Matching uses ledger_dedup's own identity key — the same
    (fact_type, element_type, normalized label) triple that already decides
    "these are the same fact" when merging within a single extraction — so
    supersession can never be looser than dedup. Only facts from a DIFFERENT
    source are retired: two facts from the same source were already merged
    by dedup, and retiring a fact against its own source would erase the
    source's own contribution.

    Returns the number of rows retired. The rows are kept (superseded=True),
    never deleted: the ledger is an audit trail of what each source said,
    and losing history on every patch would make it impossible to see why a
    requirement changed.
    """
    from app.models.sow import SowRequirementsLedger
    from app.services.ledger_dedup import normalize_label

    if not new_facts:
        return 0

    new_ids = {f.id for f in new_facts}

    def identity(fact):
        element_type = (
            fact.element_type.value
            if fact.element_type and hasattr(fact.element_type, "value")
            else fact.element_type
        )
        fact_type = (
            fact.fact_type.value if hasattr(fact.fact_type, "value") else str(fact.fact_type)
        )
        return (fact_type, element_type, normalize_label(fact.label, element_type))

    new_identities = {identity(f) for f in new_facts}
    new_source_ids = {f.source_artifact_id for f in new_facts}

    existing = (
        session.query(SowRequirementsLedger)
        .filter(
            SowRequirementsLedger.document_id == document_id,
            SowRequirementsLedger.superseded.is_(False),
        )
        .all()
    )

    retired = 0
    for fact in existing:
        if fact.id in new_ids:
            continue
        if fact.source_artifact_id in new_source_ids:
            continue  # same source — dedup already handled it
        if identity(fact) in new_identities:
            fact.superseded = True
            retired += 1

    if retired:
        logger.info(
            "SOW impact: retired %d older fact(s) restated by the new source", retired
        )
    return retired
