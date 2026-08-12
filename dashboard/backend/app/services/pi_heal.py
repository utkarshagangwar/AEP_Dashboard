"""Project Intelligence — TDD/Ledger Healing (Phase 2, spec v3.0 §19).

The highest-risk write in the whole feature — the only place this feature
ever mutates a row belonging to another domain (sow_requirements_ledger).
Every function here is written to the letter of §19.1-§19.4:

  §19.1  Regenerating a SOW stays a deliberate human action on the SOW
         screen. Healing corrects the ledger of FACTS; it never rewrites
         sow_documents, sow_document_versions, sow_sections, or the
         uploaded source file (see api/v1/project_intelligence.py's
         module docstring's "touched / not touched" split).
  §19.2  match_ledger_fact() — deterministic matching, no fuzzy/embedding
         matching (same discipline ledger_dedup.py already enforces for
         cross-chunk dedup, and for the same reason: a false match here
         silently corrupts a requirement, which is strictly worse than
         the drift it was trying to fix).
  §19.3  apply_heal() — supersede the old row, insert a new one, never an
         UPDATE. The old row is retired, never deleted (audit trail).
  §19.4  Guards — PI_HEAL_LEDGER kill switch (checked here too, in
         addition to the API layer, as defense in depth), the single-apply
         guard (applied_ledger_fact_id IS NULL), never re-matches an
         already-superseded ledger row, and no bulk path exists anywhere
         in this module — every function operates on exactly one flag.

Caller (api/v1/project_intelligence.py) controls the transaction, matching
every other Project Intelligence service — functions here only `flush()`;
the endpoint commits once, then writes the audit log, exactly like
apply_review_action already does for the other four entity types.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── §19.2 — deterministic ledger matching ────────────────────────────────

def match_ledger_fact(db, *, project_id, previous_label: str,
                       component_type: Optional[str] = None,
                       screen_route: Optional[str] = None):
    """Find the single sow_requirements_ledger row a pi_components label
    matches, per spec §19.2's rule. Returns None if there is no match —
    a drift flag is still worth creating and reviewing even with nothing
    to heal in the ledger (spec §19.1: detection and healing are
    independently useful).

    component_type is passed straight through to ledger_dedup.normalize_
    label as `element_type`. The two taxonomies (pi_components.component_
    type from RF/Vibe capture vs. SowUIElementType from ledger extraction)
    only actually overlap on 'button' and 'checkbox' — for every other
    value normalize_label's role-noun stripping simply doesn't recognise
    the key and falls through to plain normalization, which is safe (it
    can only ever normalize LESS aggressively, never cross-match two
    different roles by coincidence).

    When more than one ledger row normalizes to the same label (rare —
    two identically-labelled controls in different sections), `location`
    is used as the tie-breaker described as "optional" in spec §19.2:
    prefer a match whose location text contains a segment of the
    screen's route. This is best-effort, not a hard requirement — falling
    through to "first found" on no location signal is acceptable because
    normalize_label's role-noun-aware equality is already the primary,
    conservative filter.
    """
    from app.models.sow import SowDocument, SowLedgerFactType, SowRequirementsLedger
    from app.services import ledger_dedup

    label = (previous_label or "").strip()
    if not label:
        return None
    target_norm = ledger_dedup.normalize_label(label, component_type)
    if not target_norm:
        return None

    candidates = (
        db.query(SowRequirementsLedger)
        .join(SowDocument, SowDocument.id == SowRequirementsLedger.document_id)
        .filter(
            SowRequirementsLedger.fact_type == SowLedgerFactType.ui_element,
            SowRequirementsLedger.superseded.is_(False),
            SowDocument.project_id == project_id,
        )
        .all()
    )

    matches = []
    for row in candidates:
        element_type_value = row.element_type.value if row.element_type else None
        if ledger_dedup.normalize_label(row.label, element_type_value) == target_norm:
            matches.append(row)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    if screen_route:
        route_words = [w for w in screen_route.strip("/").replace("-", " ").split("/") if w]
        for row in matches:
            loc = (row.location or "").casefold()
            if loc and any(w.casefold() in loc for w in route_words):
                return row

    logger.info(
        "pi_heal: %d ledger rows matched label %r for project %s — using the first",
        len(matches), label, project_id,
    )
    return matches[0]


# ── §19.3 — the apply operation ──────────────────────────────────────────

def apply_heal(db, *, flag_id, actor_user_id, label: Optional[str] = None,
                behavior_notes: Optional[str] = None, confirm_pairing: bool = True):
    """Apply a pending drift flag's proposed healing.

    Returns (flag, new_ledger_row_or_None) on success. Returns (None, None)
    when the flag cannot be applied at all (not found, not pending,
    already applied, healing disabled, or — candidate_rename only —
    confirm_pairing=False, which the caller should route to reject_drift_
    flag instead of calling this function). A flag whose ledger match no
    longer holds is still resolvable: it is marked verified with no
    ledger write, and the caller is told via the returned new_ledger_row
    being None while flag is not None, so the API layer can report
    "reviewed, nothing to heal" rather than a hard failure.
    """
    from app.models.project_intelligence import PiDriftFlag, PiReviewAction, PiStatus
    from app.models.sow import SowRequirementsLedger
    from app.services import pi_ingest

    if not pi_ingest.heal_ledger_enabled():
        # Defense in depth — the API layer already 403s before calling
        # this, but a future caller must not be able to bypass the switch.
        return None, None

    flag = db.query(PiDriftFlag).filter(PiDriftFlag.id == flag_id).one_or_none()
    if flag is None or flag.status != PiStatus.pending:
        return None, None
    if flag.applied_ledger_fact_id is not None:
        return None, None  # single-apply guard (spec §19.4)
    if flag.drift_type == "candidate_rename" and not confirm_pairing:
        return None, None

    now = datetime.utcnow()

    old_ledger_row = None
    if flag.ledger_fact_id is not None:
        candidate = (
            db.query(SowRequirementsLedger)
            .filter(
                SowRequirementsLedger.id == flag.ledger_fact_id,
                SowRequirementsLedger.superseded.is_(False),
            )
            .one_or_none()
        )
        # Re-verify the match still holds (spec §19.3 step 2) — guards
        # against the ledger row having been edited or superseded by
        # something else between detection and this apply call.
        if candidate is not None:
            expected_old_label = _expected_previous_label(db, flag)
            rematch = match_ledger_fact(
                db, project_id=flag.project_id, previous_label=expected_old_label,
                screen_route=None,
            )
            if rematch is not None and rematch.id == candidate.id:
                old_ledger_row = candidate

    if old_ledger_row is None:
        # Nothing (or no longer anything) to heal in the ledger — still a
        # legitimate outcome (spec §19.1: detection has value on its own).
        flag.status = PiStatus.verified
        flag.reviewed_by = actor_user_id
        flag.reviewed_at = now
        db.add(PiReviewAction(
            project_id=flag.project_id, entity_type="drift_flag", entity_id=flag.id,
            action="approve", reason="no matching ledger fact at apply time",
            actor_user_id=actor_user_id,
        ))
        db.flush()
        return flag, None

    corrected_label = (label or flag.proposed_label or old_ledger_row.label)[:500]
    corrected_notes = (
        behavior_notes if behavior_notes is not None
        else (flag.proposed_behavior_notes or old_ledger_row.behavior_notes)
    )

    old_ledger_row.superseded = True

    new_row = SowRequirementsLedger(
        document_id=old_ledger_row.document_id,
        source_artifact_id=old_ledger_row.source_artifact_id,
        fact_type=old_ledger_row.fact_type,
        element_type=old_ledger_row.element_type,
        label=corrected_label,
        location=old_ledger_row.location,
        behavior_notes=corrected_notes,
        source_ref=f"project_intelligence:pi_drift_flags/{flag.id}"[:500],
        source_heading_path=old_ledger_row.source_heading_path,
        assigned_section_key=old_ledger_row.assigned_section_key,
        superseded=False,
    )
    db.add(new_row)
    db.flush()  # need new_row.id before it can be referenced below

    flag.status = PiStatus.verified
    flag.applied_ledger_fact_id = new_row.id
    flag.reviewed_by = actor_user_id
    flag.reviewed_at = now

    db.add(PiReviewAction(
        project_id=flag.project_id, entity_type="drift_flag", entity_id=flag.id,
        action="approve", reason=None, actor_user_id=actor_user_id,
    ))
    db.flush()
    return flag, new_row


def _expected_previous_label(db, flag) -> str:
    """The label match_ledger_fact was originally run against for this
    flag, re-derived from the live component rows (not stored verbatim on
    the flag) so re-verification reflects current state, not a stale
    snapshot."""
    from app.models.project_intelligence import PiComponent

    if flag.drift_type == "candidate_rename" and flag.candidate_component_id is not None:
        candidate = db.get(PiComponent, flag.candidate_component_id)
        return candidate.label if candidate is not None else (flag.description or "")

    component = db.get(PiComponent, flag.component_id)
    if component is None:
        return flag.description or ""
    return component.previous_label or component.label


# ── §19.4 — reversal ──────────────────────────────────────────────────────

def reverse_heal(db, *, flag_id, actor_user_id, reason: Optional[str] = None):
    """Reverse a previously-applied heal: clear superseded on the old row,
    mark the new one superseded, flip the flag to rejected. Nothing is
    deleted or updated in place — both ledger rows and the flag's history
    remain exactly as they were, just with different superseded/status
    values (spec §19.4: "Because nothing was updated in place, nothing was
    lost")."""
    from app.models.project_intelligence import PiDriftFlag, PiReviewAction, PiStatus
    from app.models.sow import SowRequirementsLedger

    flag = db.query(PiDriftFlag).filter(PiDriftFlag.id == flag_id).one_or_none()
    if flag is None or flag.status != PiStatus.verified or flag.applied_ledger_fact_id is None:
        return None

    new_row = db.get(SowRequirementsLedger, flag.applied_ledger_fact_id)
    old_row = db.get(SowRequirementsLedger, flag.ledger_fact_id) if flag.ledger_fact_id else None

    if new_row is not None:
        new_row.superseded = True
    if old_row is not None:
        old_row.superseded = False

    flag.status = PiStatus.rejected
    flag.reviewed_by = actor_user_id
    flag.reviewed_at = datetime.utcnow()

    db.add(PiReviewAction(
        project_id=flag.project_id, entity_type="drift_flag", entity_id=flag.id,
        action="reject", reason=reason or "heal reversed", actor_user_id=actor_user_id,
    ))
    db.flush()
    return flag


def reject_drift_flag(db, *, flag_id, actor_user_id, reason: str):
    """Dismiss a pending flag outright — never applied, never will be
    (spec §22: a required reason, same as every other reject path in this
    feature)."""
    from app.models.project_intelligence import PiDriftFlag, PiReviewAction, PiStatus

    flag = db.query(PiDriftFlag).filter(PiDriftFlag.id == flag_id).one_or_none()
    if flag is None or flag.status != PiStatus.pending:
        return None

    flag.status = PiStatus.rejected
    flag.reviewed_by = actor_user_id
    flag.reviewed_at = datetime.utcnow()

    db.add(PiReviewAction(
        project_id=flag.project_id, entity_type="drift_flag", entity_id=flag.id,
        action="reject", reason=reason, actor_user_id=actor_user_id,
    ))
    db.flush()
    return flag
