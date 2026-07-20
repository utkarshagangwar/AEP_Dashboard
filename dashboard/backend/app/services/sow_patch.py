"""SOW rewrite/patch orchestration (Phase 7 — plan §2/§4/§11.4).

Complements generate_sow_task's full-regeneration path (Phase 3): where
POST .../generate always throws away the current version and redrafts
every section from scratch, POST .../rewrite (this module's consumer)
produces a new version that copies every UNTARGETED section forward
unchanged from the parent version, and only re-drafts+re-audits the
caller's chosen target_sections — "the AI got this one section wrong, try
again" without paying to regenerate (and re-risk) everything else.

Scope, deliberately bounded for this first cut (documented, not hidden):
  - Patch does NOT re-run ledger extraction (Pass 1). New source material
    is incorporated via the existing Phase 1 "attach a source" flow
    (which already re-extracts into the ledger); a subsequent PATCH or
    full /generate is what turns freshly-extracted facts into drafted
    prose. A patched section is re-drafted from whatever facts are
    ALREADY assigned to its section_key
    (SowRequirementsLedger.assigned_section_key, stamped by
    generate_sow_task's Pass 2a) — so patch is for "redo the
    writing/audit for this section," not "incorporate this brand-new fact
    I just added." Full /generate remains the way to fold new material
    into the section groupings themselves.
  - Only functional (ledger-grouped) sections are patchable. Project
    Overview/Scope of Work are drafted from the WHOLE document's facts,
    not one section's assigned subset, so "patch just this section" isn't
    well-defined for them the same way sow_drafting.draft_section's
    per-section facts are; the five templated trailing sections have no
    facts at all and are meant to be hand-edited directly via
    PATCH .../sections instead (Phase 5). Both rejected with a clear 400
    by the API layer using non_patchable_section_keys() below.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.design_ingest import IngestError

logger = get_logger(__name__)


def non_patchable_section_keys() -> set[str]:
    """project-overview/scope-of-work (whole-document framing, not one
    section's fact subset) + the five templated trailing sections (no
    facts to redraft from at all)."""
    from app.services.sow_assembly import build_templated_sections

    templated = {s["section_key"] for s in build_templated_sections()}
    return templated | {"project-overview", "scope-of-work"}


def filter_protected_sections(
    target_sections: list[str],
    parent_sections_by_key: dict,
    override_manual_edits: list[str],
) -> tuple[list[str], list[str]]:
    """Plan §11.4 human-edit protection: a target section that was hand-
    edited is skipped (copied through unchanged by the caller, not
    regenerated) unless its key is also listed in override_manual_edits.
    Returns (keys_to_regenerate, keys_skipped_as_protected), preserving
    the input order of target_sections in both."""
    override_set = set(override_manual_edits or [])
    to_regen: list[str] = []
    skipped: list[str] = []
    for key in target_sections:
        parent = parent_sections_by_key.get(key)
        if parent is not None and parent.edited_by_human and key not in override_set:
            skipped.append(key)
        else:
            to_regen.append(key)
    return to_regen, skipped


def draft_and_audit_section(heading: str, facts: list) -> dict:
    """Pass 2 (draft) + Pass 3 (audit) for one section's fact set — shared
    by generate_sow_task's Pass 2b loop and patch_sow_task so a fix to
    this logic can't drift between the full-generation and patch paths.

    Never raises: a drafting failure is captured into the returned
    status/error_message; an audit failure degrades to
    coverage_score=None without affecting the drafting result. Both
    behaviors match what generate_sow_task already did inline before this
    was extracted — this is a refactor of existing, already-verified
    behavior, not a behavior change.

    Returns {status, content_blocks, error_message, coverage_score,
    coverage_gaps, models_used (set[str])}.
    """
    from app.models.sow import SowSectionStatus
    from app.services import sow_audit, sow_drafting

    models_used: set[str] = set()

    try:
        blocks, draft_model = sow_drafting.draft_section(heading, facts)
    except IngestError as exc:
        logger.warning("SOW draft_and_audit_section: drafting failed for '%s': %s", heading, exc)
        return {
            "status": SowSectionStatus.error,
            "content_blocks": [{"type": "heading", "level": 2, "text": heading}],
            "error_message": str(exc),
            "coverage_score": None,
            "coverage_gaps": None,
            "models_used": models_used,
        }
    models_used.add(draft_model)

    coverage_score = None
    coverage_gaps = None
    try:
        coverage_score, coverage_gaps, audit_model = sow_audit.audit_section(heading, blocks, facts)
        if audit_model:
            models_used.add(audit_model)
    except IngestError as exc:
        logger.warning(
            "SOW draft_and_audit_section: audit failed for '%s': %s — keeping the draft, "
            "coverage_score stays null", heading, exc,
        )

    return {
        "status": SowSectionStatus.done,
        "content_blocks": blocks,
        "error_message": None,
        "coverage_score": coverage_score,
        "coverage_gaps": coverage_gaps,
        "models_used": models_used,
    }
