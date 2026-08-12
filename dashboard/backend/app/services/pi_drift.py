"""Project Intelligence — Change Detection (Phase 2, spec v3.0 §18).

Classifies what a re-observed pi_components row (or a screen re-visit as a
whole) tells us against what was already known, per spec table 10:

    label_changed       same key, different label            -> pi_change_log + pi_drift_flags
    candidate_rename     vanished tier-5 key + new tier-5 key  -> pi_change_log + pi_drift_flags
                          on the same screen/type, high text
                          similarity
    locator_broken       same key, locator no longer resolves -> pi_change_log only
    removed               key absent for N consecutive visits  -> pi_change_log only
    added                  new key                              -> pi_change_log only
    behavior_changed      same key, materially different       -> pi_change_log + pi_drift_flags
                          observed outcome

Per spec §18.3: "Every classification writes a pi_change_log row. Only
label_changed, behavior_changed and confirmed candidate_rename pairs raise
a pi_drift_flag — the others are history, not review work." This module
follows that split exactly: locator_broken/removed/added are logged for
the audit trail and (for locator_broken) future locator self-healing
(Phase 4), but never create review-queue work by themselves.

DETERMINISTIC FIRST (spec §18.1): every classification decision above is a
lookup or a threshold comparison, never an LLM call. The one LLM call in
this module writes the human-readable `description` / `proposed_label` /
`proposed_behavior_notes` text on a PiDriftFlag AFTER the classification
has already been decided — exactly mirroring pi_extract._maybe_write_
behavior_note's contract (best-effort, deterministic fallback, gated by
the same PI_CAPTURE_BEHAVIOR_NOTES flag, never blocks flag creation).

INTERPRETATION NOTE on `behavior_changed` (flagged honestly, same spirit as
the Phase 1 report's disclosed simplifications): spec table 10 defines it
only as "same key, materially different observed outcome" without a
deterministic test. This module uses a conservative, documented proxy: an
outcome-success edge transition (last success -> this failure) on a
component with NO real locator (tier 5, e.g. every Vibe-run action) is
classified behavior_changed; the identical transition on a component WITH
a real locator (tiers 1-4) is classified locator_broken instead. The
reasoning: a tier 1-4 failure most plausibly means the control moved or
was renamed at the DOM level (a locator problem); a tier-5 failure has no
locator to break, so a changed outcome more plausibly reflects a changed
business rule. This is a proxy, not a semantic diff — flagged here so a
future Phase 2.1 pass can replace it without guessing why it exists.

Fails open, always: every public function here is called from
pi_extract.process_capture_event, which already wraps the whole capture in
try/except (see that module's docstring) — but each function here ALSO
catches its own exceptions so one bad classification cannot lose the
screen/component/edge extraction that already succeeded in the same pass.
"""
from __future__ import annotations

import difflib
from datetime import datetime
from typing import Any, Optional

from app.core.logging import get_logger
from app.services import pi_extract, pi_ingest

logger = get_logger(__name__)

_STABLE_TIER_MAX_FOR_LABEL_CHANGE = 3  # spec table 10: "Only produced at tiers 1-3"

_SEVERITY_BY_DRIFT_TYPE = {
    "label_changed": "medium",
    "behavior_changed": "medium",
    "candidate_rename": "low",   # unconfirmed pairing — human judgement first
    "locator_broken": "high",    # breaks automation immediately (not flagged yet, kept for Phase 4)
}


def _normalize_for_similarity(text: str) -> str:
    return " ".join((text or "").strip().casefold().split())


def _write_change_log(db, *, project_id, entity_id, change_type: str,
                       previous_value: Optional[dict], new_value: Optional[dict],
                       detected_by_run_id=None):
    from app.models.project_intelligence import PiChangeLog

    try:
        db.add(PiChangeLog(
            project_id=project_id,
            entity_type="component",
            entity_id=entity_id,
            change_type=change_type,
            previous_value=previous_value,
            new_value=new_value,
            detected_by_run_id=detected_by_run_id,
        ))
        db.flush()
    except Exception:  # noqa: BLE001 — history must never break extraction
        logger.warning(
            "pi_drift: could not write change_log entry (%s) for component %s",
            change_type, entity_id, exc_info=True,
        )


# ── Description generation (the one LLM call, best-effort) ──────────────────

_DRIFT_DESCRIPTION_SYSTEM = (
    "You write a ONE-sentence, plain-language explanation of a detected change "
    "in a web application's UI, for a QA reviewer deciding whether to accept a "
    "proposed correction. Be concrete and factual; never invent detail beyond "
    "what is given. "
    'Respond with JSON only: {"description": "<one sentence, <=280 chars>"}.'
)


def _describe_drift(drift_type: str, *, screen_route: str, old_label: Optional[str],
                     new_label: Optional[str]) -> str:
    """Best-effort one-sentence explanation. Falls back to a deterministic
    template on any failure (flag off, LLM error, bad JSON) — a drift flag
    is always created with SOME description; a prettier one is a bonus,
    never a precondition (same rule as pi_extract's behavior notes)."""
    fallback = {
        "label_changed": f'Label changed from "{old_label}" to "{new_label}" on {screen_route}.',
        "behavior_changed": f'"{old_label}" on {screen_route} now behaves differently than previously observed.',
        "candidate_rename": (
            f'"{old_label}" on {screen_route} appears to have been replaced by "{new_label}" '
            f'— both have unstable locators, so this is a candidate pairing, not a confirmed rename.'
        ),
    }.get(drift_type, f"Change detected on {screen_route}.")

    if not pi_extract.behavior_notes_enabled():
        return fallback
    try:
        from app.services import llm_router

        prompt = (
            f"Change type: {drift_type}\nScreen: {screen_route}\n"
            f"Previous label: {old_label!r}\nNew/current label: {new_label!r}"
        )
        result = llm_router.complete_json_complete(
            prompt, system=_DRIFT_DESCRIPTION_SYSTEM, max_tokens=200,
        )
        parsed = result.parsed_json or {}
        description = str(parsed.get("description") or "").strip()
        return description[:2000] if description else fallback
    except Exception:  # noqa: BLE001
        logger.warning("pi_drift: description generation failed for %s (%s)",
                        drift_type, screen_route, exc_info=True)
        return fallback


def _create_drift_flag(db, *, project_id, screen_row, component_row, drift_type: str,
                        old_label: Optional[str], new_label: Optional[str],
                        candidate_component_id=None, proposed_behavior_notes: Optional[str] = None):
    """Write one pi_drift_flags row, resolving a candidate ledger match
    (spec §19.2) via services.pi_heal. Never raises — a failure here still
    leaves the pi_change_log entry (already written by the caller) as the
    durable record of what was detected."""
    from app.models.project_intelligence import PiDriftFlag
    from app.services import pi_heal

    try:
        description = _describe_drift(
            drift_type, screen_route=screen_row.route, old_label=old_label, new_label=new_label,
        )
        ledger_fact = None
        try:
            ledger_fact = pi_heal.match_ledger_fact(
                db, project_id=project_id, previous_label=old_label or new_label or "",
                component_type=component_row.component_type, screen_route=screen_row.route,
            )
        except Exception:  # noqa: BLE001 — a missing match is fine, a crash is not
            logger.warning("pi_drift: ledger match lookup failed for %s", component_row.id,
                            exc_info=True)

        flag = PiDriftFlag(
            project_id=project_id,
            screen_id=screen_row.id,
            component_id=component_row.id,
            candidate_component_id=candidate_component_id,
            ledger_fact_id=ledger_fact.id if ledger_fact is not None else None,
            drift_type=drift_type,
            severity=_SEVERITY_BY_DRIFT_TYPE.get(drift_type, "medium"),
            description=description,
            proposed_label=(new_label[:500] if new_label else None),
            proposed_behavior_notes=proposed_behavior_notes,
            identity_tier=component_row.identity_tier,
        )
        db.add(flag)
        db.flush()
        return flag
    except Exception:  # noqa: BLE001 — the change_log entry already survives without this
        logger.warning("pi_drift: could not create drift flag (%s) for component %s",
                        drift_type, component_row.id, exc_info=True)
        return None


def _has_open_flag(db, *, component_id, drift_type: str, candidate_component_id=None) -> bool:
    """Dedup guard: at most one PENDING flag per (component, drift_type[,
    candidate]) — a persisting, unreviewed discrepancy must not spam a new
    row on every subsequent visit until a human acts on it. Application-
    level (query-then-insert), matching how _resolve_screen/_resolve_
    component already do find-or-create — no DB constraint backs this."""
    from app.models.project_intelligence import PiDriftFlag, PiStatus

    query = db.query(PiDriftFlag.id).filter(
        PiDriftFlag.component_id == component_id,
        PiDriftFlag.drift_type == drift_type,
        PiDriftFlag.status == PiStatus.pending,
    )
    if candidate_component_id is not None:
        query = query.filter(PiDriftFlag.candidate_component_id == candidate_component_id)
    return query.first() is not None


# ── Per-action classification (label_changed / locator_broken / behavior_changed) ──

def classify_reobservation(db, *, project_id, screen_row, component_row, action: dict) -> None:
    """Called for every action resolved against an EXISTING component
    (pi_extract._resolve_component's created=False path). component_row's
    label/locator are still exactly as first recorded (Phase 1 never
    rewrites them), so comparing them against `action` IS the diff."""
    try:
        observed_label = str(action.get("label") or "").strip()
        observed_success = bool(action.get("outcome_success", True))

        # -- label_changed (tiers 1-3 only, spec table 10) --------------
        if (
            observed_label
            and component_row.identity_tier <= _STABLE_TIER_MAX_FOR_LABEL_CHANGE
            and _normalize_for_similarity(observed_label)
                != _normalize_for_similarity(component_row.label)
        ):
            old_label = component_row.label
            _write_change_log(
                db, project_id=project_id, entity_id=component_row.id,
                change_type="label_changed",
                previous_value={"label": old_label}, new_value={"label": observed_label},
            )
            component_row.previous_label = old_label
            db.flush()
            if not _has_open_flag(db, component_id=component_row.id, drift_type="label_changed"):
                _create_drift_flag(
                    db, project_id=project_id, screen_row=screen_row, component_row=component_row,
                    drift_type="label_changed", old_label=old_label, new_label=observed_label,
                )

        # -- locator_broken / behavior_changed (edge transition only) ---
        prior_success = component_row.last_outcome_success
        if prior_success is True and observed_success is False:
            has_real_locator = bool(component_row.locator) and component_row.identity_tier <= 4
            if has_real_locator:
                _write_change_log(
                    db, project_id=project_id, entity_id=component_row.id,
                    change_type="locator_broken",
                    previous_value={"locator": component_row.locator},
                    new_value={"outcome": "fail"},
                )
                # Not flagged (spec §18.3) — feeds Phase 4 locator
                # self-healing directly via fail_count, already tracked.
            else:
                _write_change_log(
                    db, project_id=project_id, entity_id=component_row.id,
                    change_type="behavior_changed",
                    previous_value={"outcome": "success"}, new_value={"outcome": "fail"},
                )
                if not _has_open_flag(db, component_id=component_row.id, drift_type="behavior_changed"):
                    _create_drift_flag(
                        db, project_id=project_id, screen_row=screen_row, component_row=component_row,
                        drift_type="behavior_changed", old_label=component_row.label,
                        new_label=component_row.label,
                        proposed_behavior_notes=(
                            f'"{component_row.label}" on {screen_row.route} no longer succeeds '
                            f"as it previously did — needs a fresh behaviour description."
                        ),
                    )

        component_row.last_outcome_success = observed_success
        db.flush()
    except Exception:  # noqa: BLE001 — classification is auxiliary to extraction
        logger.warning("pi_drift: classify_reobservation failed for component %s",
                        getattr(component_row, "id", None), exc_info=True)


def classify_added(db, *, project_id, component_row) -> None:
    """Called for every NEWLY created component (pi_extract._resolve_
    component's created=True path). History only — never flagged."""
    _write_change_log(
        db, project_id=project_id, entity_id=component_row.id, change_type="added",
        previous_value=None,
        new_value={"label": component_row.label, "component_type": component_row.component_type},
    )


# ── Per-screen-visit reconciliation (removed / candidate_rename) ────────────

def reconcile_screen_visit(db, *, project_id, screen_row, observed_component_ids: set) -> list:
    """Called once per screen visit, after every action on it has been
    resolved. Increments/resets missed_streak for every currently-active
    component on this screen, and logs `removed` (edge-triggered, once) for
    any that just crossed pi_ingest.removed_threshold().

    Returns the list of components that just transitioned from
    missed_streak 0 -> 1 THIS visit (i.e. freshly, not yet `removed`) and
    are tier 5 — candidate_rename pairing material for the caller.
    """
    from app.models.project_intelligence import PiComponent, PiStatus

    freshly_vanished_tier5 = []
    try:
        threshold = pi_ingest.removed_threshold()
        active = (
            db.query(PiComponent)
            .filter(
                PiComponent.project_id == project_id,
                PiComponent.screen_id == screen_row.id,
                PiComponent.status.in_((PiStatus.pending, PiStatus.verified)),
            )
            .all()
        )
        for comp in active:
            if comp.id in observed_component_ids:
                if comp.missed_streak:
                    comp.missed_streak = 0
                continue

            previous_streak = comp.missed_streak or 0
            comp.missed_streak = previous_streak + 1

            if previous_streak == 0 and comp.identity_tier == 5:
                freshly_vanished_tier5.append(comp)

            if previous_streak < threshold <= comp.missed_streak:
                _write_change_log(
                    db, project_id=project_id, entity_id=comp.id, change_type="removed",
                    previous_value={"label": comp.label}, new_value=None,
                )
                # Not flagged (spec §18.3) — history only.

        db.flush()
    except Exception:  # noqa: BLE001
        logger.warning("pi_drift: reconcile_screen_visit failed for screen %s",
                        getattr(screen_row, "id", None), exc_info=True)
    return freshly_vanished_tier5


def maybe_pair_candidate_renames(db, *, project_id, screen_row,
                                  newly_created_tier5: list, freshly_vanished_tier5: list) -> None:
    """Pair a freshly-vanished tier-5 component with a newly-created tier-5
    component of the same type on the same screen, by text similarity
    (spec §18.4 — deliberately conservative, same reasoning as
    skill_store.find_similar_skill: picks the single closest match above
    threshold, not just the first, so the result doesn't depend on
    iteration order). NEVER auto-applied — only ever creates a pending
    pi_drift_flags row for a human to confirm or split."""
    if not newly_created_tier5 or not freshly_vanished_tier5:
        return
    try:
        threshold = pi_ingest.rename_similarity_threshold()
        used_vanished_ids = set()

        for new_comp in newly_created_tier5:
            best = None
            best_ratio = 0.0
            new_norm = _normalize_for_similarity(new_comp.label)
            for old_comp in freshly_vanished_tier5:
                if old_comp.id in used_vanished_ids:
                    continue
                if old_comp.component_type != new_comp.component_type:
                    continue
                old_norm = _normalize_for_similarity(old_comp.label)
                if not old_norm or not new_norm:
                    continue
                ratio = difflib.SequenceMatcher(None, old_norm, new_norm).ratio()
                if ratio > best_ratio:
                    best_ratio, best = ratio, old_comp

            if best is None or best_ratio < threshold:
                continue
            used_vanished_ids.add(best.id)

            _write_change_log(
                db, project_id=project_id, entity_id=new_comp.id, change_type="candidate_rename",
                previous_value={"label": best.label, "component_id": str(best.id)},
                new_value={"label": new_comp.label, "similarity": round(best_ratio, 3)},
            )
            if not _has_open_flag(
                db, component_id=new_comp.id, drift_type="candidate_rename",
                candidate_component_id=best.id,
            ):
                _create_drift_flag(
                    db, project_id=project_id, screen_row=screen_row, component_row=new_comp,
                    drift_type="candidate_rename", old_label=best.label, new_label=new_comp.label,
                    candidate_component_id=best.id,
                )
        db.flush()
    except Exception:  # noqa: BLE001
        logger.warning("pi_drift: maybe_pair_candidate_renames failed for screen %s",
                        getattr(screen_row, "id", None), exc_info=True)
