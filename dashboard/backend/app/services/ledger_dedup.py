"""Cross-chunk deduplication of SOW requirements-ledger facts.

SOW_CHUNKING_PLAN.md Phase 4 (defect D3). Before this module,
sow_ledger.extract_ledger_from_*_full() concatenated per-chunk results with
a plain list.extend() and an explicit "no cross-chunk dedup (Phase 1 scope)"
comment. A control described in three sections of a SOW produced three
ledger rows, which becomes three duplicate vibe tests, three duplicate
audit entries, and a coverage number inflated by restatement.

DETERMINISTIC ONLY -- NO FUZZY OR SEMANTIC MATCHING
---------------------------------------------------
Merging is keyed on (fact_type, element_type, normalised label). That is
deliberately conservative. Fuzzy or embedding-based matching is where FALSE
merges come from, and a false merge silently destroys a real requirement --
strictly worse than the duplicate it was trying to remove. "Delete" and
"Delete selected" both survive here; that is a known, accepted gap (plan
§2.4), not an oversight.

The same conservatism drives the merge rules below: when two facts disagree
about a field, the union is kept wherever a union is meaningful (locations,
source refs) rather than one value being discarded.

SCOPE: within a single source's extraction run only. Cross-SOURCE dedup
(the same control found in both a transcript and a design image) is a
document-level concern and belongs with the parent plan's Phase 2
constrained regrouping -- doing it here would silently merge facts whose
`source_artifact_id` differs, destroying the traceability the ledger exists
to provide.
"""
from __future__ import annotations

import re

from app.core.logging import get_logger

logger = get_logger(__name__)

# Mirrors app.models.sow.SowRequirementsLedger.source_ref (String(500)).
# Enforced here so a merged value can never fail the INSERT -- a DataError
# at commit time would lose an entire extraction run's facts.
_MAX_SOURCE_REF_CHARS = 500
_MAX_SOURCE_REFS_KEPT = 3
_MAX_LOCATION_CHARS = 500
_MAX_NOTES_CHARS = 2000

_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_PUNCT_STRIP_RE = re.compile(r"^[\s\"'`(\[]+|[\s\"'`)\].,;:!?]+$")
_WHITESPACE_RE = re.compile(r"\s+")

# Only stripped when element_type ALREADY encodes the control kind -- see
# normalize_label. Without that guard, "Delete button" and "Delete dropdown"
# would collapse into one fact.
_ROLE_NOUN_BY_ELEMENT = {
    "button": ("button", "btn"),
    "dropdown": ("dropdown", "drop down", "select", "selector"),
    "filter": ("filter",),
    "checkbox": ("checkbox", "check box"),
    "toggle": ("toggle", "switch"),
    "slider": ("slider",),
    "three_dot_menu": ("menu", "kebab menu", "three dot menu", "three-dot menu"),
    "tab": ("tab",),
    "modal": ("modal", "dialog", "popup", "pop up"),
}


def normalize_label(label: str, element_type: str | None = None) -> str:
    """Canonical form of a fact label for equality comparison.

    casefold -> collapse whitespace -> strip wrapping punctuation -> drop a
    leading article -> drop a trailing role noun IF element_type already
    carries that information.

    The role-noun rule is what makes "Delete button" and "the Delete
    button" and "Delete" collapse when all three are element_type=button,
    while keeping them distinct from a fact typed as a dropdown.
    """
    text = _WHITESPACE_RE.sub(" ", (label or "")).strip().casefold()
    text = _PUNCT_STRIP_RE.sub("", text)
    text = _LEADING_ARTICLE_RE.sub("", text).strip()

    for noun in _ROLE_NOUN_BY_ELEMENT.get(element_type or "", ()):
        if text.endswith(" " + noun):
            text = text[: -(len(noun) + 1)].strip()
            break
        if text == noun:
            # The label is ONLY the role noun ("button"). Nothing else
            # identifies it, so leave it intact rather than reducing it to
            # an empty key that would swallow every other fact.
            break

    return text


def _merge_locations(existing: str | None, incoming: str | None) -> str | None:
    """Union, not overwrite. A control that appears on two pages is genuinely
    two placements, and discarding one loses a real requirement."""
    if not existing:
        return incoming
    if not incoming or incoming.strip().casefold() in existing.casefold():
        return existing
    return f"{existing}; {incoming}"[:_MAX_LOCATION_CHARS]


def _merge_notes(existing: str | None, incoming: str | None) -> str | None:
    """Keep the richer description, then append anything the shorter one said
    that the longer one did not.

    Two chunks often describe the same control from different angles ("opens
    a confirmation modal" / "disabled when nothing is selected"). Keeping
    only the longest would drop the second, which is exactly the kind of
    detail the ledger's exhaustiveness contract exists to preserve.
    """
    if not existing:
        return incoming
    if not incoming:
        return existing

    longer, shorter = (existing, incoming) if len(existing) >= len(incoming) else (incoming, existing)
    longer_lower = longer.casefold()
    additions = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", shorter)
        if sentence.strip() and sentence.strip().casefold() not in longer_lower
    ]
    if not additions:
        return longer[:_MAX_NOTES_CHARS]
    return f"{longer} {' '.join(additions)}".strip()[:_MAX_NOTES_CHARS]


def _merge_source_refs(existing: str | None, incoming: str | None) -> str | None:
    """Join distinct refs so a merged fact stays traceable to every place it
    was found -- traceability is the whole point of source_ref."""
    refs: list[str] = []
    for value in (existing, incoming):
        if not value:
            continue
        for part in value.split(";"):
            part = part.strip()
            if part and part not in refs:
                refs.append(part)
    if not refs:
        return None
    return "; ".join(refs[:_MAX_SOURCE_REFS_KEPT])[:_MAX_SOURCE_REF_CHARS]


def dedupe_facts(facts: list[dict]) -> tuple[list[dict], int]:
    """Merge duplicate facts. Returns (facts, merged_count).

    Order-independent in content: dedupe(a + b) and dedupe(b + a) produce
    the same set of merged facts. Output preserves first-seen order, which
    keeps ledger rows in roughly document order for a human reader.

    Never raises. A fact missing an expected key is passed through untouched
    rather than dropped -- validation is sow_ledger's job, and silently
    losing a fact here would be indistinguishable from the D3 bug this
    module exists to fix.
    """
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    collisions = 0

    for fact in facts:
        if not isinstance(fact, dict) or not fact.get("label"):
            continue

        element_type = fact.get("element_type")
        key = (
            fact.get("fact_type"),
            element_type,
            normalize_label(fact["label"], element_type),
        )

        if key not in merged:
            merged[key] = dict(fact)
            order.append(key)
            continue

        collisions += 1
        target = merged[key]
        # Keep the most specific surface form of the label.
        if len(fact["label"]) > len(target.get("label") or ""):
            target["label"] = fact["label"]
        target["location"] = _merge_locations(target.get("location"), fact.get("location"))
        target["behavior_notes"] = _merge_notes(
            target.get("behavior_notes"), fact.get("behavior_notes")
        )
        target["source_ref"] = _merge_source_refs(
            target.get("source_ref"), fact.get("source_ref")
        )

    if collisions:
        logger.info(
            "ledger dedup: merged %d duplicate fact(s); %d -> %d rows",
            collisions, len(facts), len(order),
        )

    return [merged[k] for k in order], collisions
