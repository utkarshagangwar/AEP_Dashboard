"""Project Intelligence — Extraction layer.

Turns the normalised observations `pi_ingest` produced (and the
`pi_capture_events` row they were written into) into pi_screens /
pi_components / pi_navigation_edges rows, plus — for a screen seen for the
first time — one plain-language pi_behavior_notes row.

PHASE 1 / PHASE 2 BOUNDARY: component resolution in this module (_resolve_
component) is STILL insert-if-new + bump-counts-on-reobservation only — it
never rewrites an existing component's label or locator itself. All of the
"did this change, and does it need review" reasoning (label_changed /
locator_broken / behavior_changed / removed / added / candidate_rename,
spec §18) lives in services/pi_drift.py, called from process_capture_event
below once a screen visit's screens/components/edges have already been
resolved. This keeps entity resolution and change classification as two
separable concerns — pi_drift.py hands back nothing that mutates identity,
it only reads the already-resolved rows and this visit's raw actions.

Deterministic by default (spec §18.1): screen/component/edge identity is
resolved by lookup and hashing, never by an LLM call. The one LLM call in
this module is behavior-note summarization — genuinely ambiguous
(turning a list of clicks into a sentence a reviewer can approve at a
glance is not a lookup) and is skipped entirely, same as everywhere else in
this feature, if the call fails or the flag is off.

Fails open, always: every path a worker task calls is wrapped so a bad
capture event degrades to "nothing extracted from this one event" rather
than blocking the test run or worker loop that queued it. This mirrors the
rule flow_validation.py and ui_inventory.py already follow.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core.logging import get_logger
from app.services import pi_ingest

logger = get_logger(__name__)


def behavior_notes_enabled() -> bool:
    return pi_ingest.pi_enabled() and pi_ingest._opt_out_flag("PI_CAPTURE_BEHAVIOR_NOTES")


# ── Screen resolution ────────────────────────────────────────────────────

def _resolve_screen(db, *, project_id, environment_id, route: str, title: Optional[str],
                     source_type: str):
    """Find-or-create the active pi_screens row for `route`.

    Matched on (project_id, route) only — route is deliberately compared
    across environments (see pi_ingest._normalize_route's docstring on why
    the host is stripped), so the same logical screen observed against a
    staging and a prod environment resolves to one row. `environment_id` is
    stored only on first insert and is never overwritten by a later
    observation from a different environment.

    Returns (screen_row, created: bool).
    """
    from app.models.project_intelligence import PiScreen, PiStatus

    now = datetime.utcnow()
    row = (
        db.query(PiScreen)
        .filter(
            PiScreen.project_id == project_id,
            PiScreen.route == route,
            PiScreen.status.in_((PiStatus.pending, PiStatus.verified)),
        )
        .order_by(PiScreen.first_seen_at.asc())
        .first()
    )
    if row is not None:
        row.last_seen_at = now
        if not row.title and title:
            row.title = title[:300]
        return row, False

    row = PiScreen(
        project_id=project_id,
        environment_id=environment_id,
        route=route[:500],
        title=(title or None) and title[:300],
        source_type=source_type[:30],
        status=PiStatus.pending,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(row)
    db.flush()
    return row, True


# ── Component resolution ─────────────────────────────────────────────────

_STABLE_STRATEGIES = ("data-testid", "id", "name", "aria-label", "role-position")


def _anchor_for_action(action: dict) -> str:
    """The value pi_ingest.compute_component_key hashes on.

    A stable-strategy locator is used verbatim (it is the whole point of
    those strategies — see classify_identity_tier). Anything else falls
    back to the normalized label text, which is exactly what makes a tier-5
    "identity" only a candidate: the anchor IS the label, so a label change
    is indistinguishable from a different control by construction.
    """
    locator = action.get("locator")
    strategy = str(action.get("locator_strategy") or "").strip().lower()
    if locator and strategy in _STABLE_STRATEGIES:
        return str(locator)
    return str(action.get("label") or "").strip().lower()


def _resolve_component(db, *, project_id, screen_row, action: dict):
    """Find-or-create the active pi_components row for one observed action.

    On reobservation: bumps success_count/fail_count and last_seen_at ONLY.
    label/locator/locator_strategy/identity_tier are left exactly as first
    recorded — this function never corrects them itself. A label/behaviour
    change is detected (not applied) by pi_drift.classify_reobservation,
    called from process_capture_event after this function returns; the
    only paths that ever change a stored label are a reviewer's explicit
    edit (PATCH .../components/{id}) or an approved ledger heal
    (services/pi_heal.py) writing to previous_label.

    Returns (component_row, created: bool), or (None, False) if the action
    carries nothing worth keying on (no label at all).
    """
    from app.models.project_intelligence import PiComponent, PiStatus

    label = str(action.get("label") or "").strip()
    component_type = str(action.get("component_type") or "other")[:30]
    if not label:
        return None, False

    anchor = _anchor_for_action(action)
    if not anchor:
        return None, False

    component_key = pi_ingest.compute_component_key(screen_row.route, component_type, anchor)
    now = datetime.utcnow()
    success = bool(action.get("outcome_success", True))

    row = (
        db.query(PiComponent)
        .filter(
            PiComponent.project_id == project_id,
            PiComponent.screen_id == screen_row.id,
            PiComponent.component_key == component_key,
            PiComponent.status.in_((PiStatus.pending, PiStatus.verified)),
        )
        .one_or_none()
    )
    if row is not None:
        row.last_seen_at = now
        if success:
            row.success_count = (row.success_count or 0) + 1
        else:
            row.fail_count = (row.fail_count or 0) + 1
        return row, False

    locator = action.get("locator")
    row = PiComponent(
        project_id=project_id,
        screen_id=screen_row.id,
        component_key=component_key,
        identity_tier=pi_ingest.classify_identity_tier(action.get("locator_strategy")),
        component_type=component_type,
        label=label[:500],
        locator=(str(locator)[:2000] if locator else None),
        locator_strategy=(str(action.get("locator_strategy"))[:50]
                           if action.get("locator_strategy") else None),
        success_count=1 if success else 0,
        fail_count=0 if success else 1,
        status=PiStatus.pending,
        last_seen_at=now,
    )
    db.add(row)
    db.flush()
    return row, True


# ── Navigation edge resolution ───────────────────────────────────────────

def _resolve_edge(db, *, project_id, from_screen_id, to_screen_id, trigger_action: Optional[str]):
    from app.models.project_intelligence import PiNavigationEdge, PiStatus

    if from_screen_id == to_screen_id:
        return None, False

    now = datetime.utcnow()
    row = (
        db.query(PiNavigationEdge)
        .filter(
            PiNavigationEdge.project_id == project_id,
            PiNavigationEdge.from_screen_id == from_screen_id,
            PiNavigationEdge.to_screen_id == to_screen_id,
            PiNavigationEdge.status.in_((PiStatus.pending, PiStatus.verified)),
        )
        .one_or_none()
    )
    if row is not None:
        row.observed_count = (row.observed_count or 0) + 1
        row.last_observed_at = now
        return row, False

    row = PiNavigationEdge(
        project_id=project_id,
        from_screen_id=from_screen_id,
        to_screen_id=to_screen_id,
        trigger_action=(trigger_action[:300] if trigger_action else None),
        observed_count=1,
        last_observed_at=now,
        status=PiStatus.pending,
    )
    db.add(row)
    db.flush()
    return row, True


# ── Behavior notes (the one LLM call in this module) ─────────────────────

_BEHAVIOR_NOTE_SYSTEM = (
    "You summarise what a screen in a web application lets a user do, from a "
    "raw list of UI actions observed on it during an automated test run. "
    "Write ONE short plain-language sentence a QA reviewer can approve or "
    "reject at a glance — describe the screen's purpose and its main "
    "interactions, not a step-by-step transcript. Do not invent behaviour "
    "that is not implied by the actions given. "
    'Respond with JSON only: {"description": "<one sentence, <=240 chars>", '
    '"confidence": <0.0-1.0, your confidence this description is accurate>}.'
)
_MAX_ACTIONS_IN_PROMPT = 25


def _maybe_write_behavior_note(db, *, project_id, screen_row, actions: list[dict],
                                source_type: str, source_ref: str):
    """Best-effort: one behavior note per newly-discovered screen.

    Only called for a screen created in this extraction pass (see
    process_capture_event) — a screen observed for the tenth time does not
    pay for another model call, and Phase 1 never rewrites a note anyway.
    Any failure (flag off, no actions, LLM error, bad JSON) is a no-op:
    a missing behavior note is strictly worse UX, never a broken run.
    """
    if not behavior_notes_enabled() or not actions:
        return None
    try:
        from app.services import llm_router

        lines = []
        for action in actions[:_MAX_ACTIONS_IN_PROMPT]:
            label = str(action.get("label") or "").strip()
            if label:
                lines.append(f"- ({action.get('component_type') or 'other'}) {label}")
        if not lines:
            return None

        prompt = (
            f"Screen route: {screen_row.route}\n"
            f"Observed actions on this screen:\n" + "\n".join(lines)
        )
        result = llm_router.complete_json_complete(
            prompt, system=_BEHAVIOR_NOTE_SYSTEM, max_tokens=300,
        )
        parsed = result.parsed_json or {}
        description = str(parsed.get("description") or "").strip()
        if not description:
            return None

        from app.models.project_intelligence import PiBehaviorNote, PiStatus

        confidence = parsed.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        note = PiBehaviorNote(
            project_id=project_id,
            screen_id=screen_row.id,
            description=description[:2000],
            source_type=source_type[:30],
            source_ref=source_ref[:500] if source_ref else None,
            confidence=confidence,
            status=PiStatus.pending,
        )
        db.add(note)
        db.flush()
        return note
    except Exception:  # noqa: BLE001 — a summary is never worth a broken run
        logger.warning(
            "pi_extract: behavior-note generation failed for project %s screen %s",
            project_id, getattr(screen_row, "id", None), exc_info=True,
        )
        return None


# ── Entry point ───────────────────────────────────────────────────────────

def process_capture_event(db, event) -> dict[str, Any]:
    """Resolve one pi_capture_events row into screens/components/edges/notes.

    Expects event.payload_json shaped as:
        {"environment_id": "<uuid str>" | None,
         "screens": [{"route": str, "title": str|None, "actions": [...]}]}
    — the "screens" list is exactly pi_ingest.normalize_rf_events /
    normalize_vibe_history's return shape; the worker hook that calls
    write_capture_event is responsible for wrapping it with
    environment_id (see workers/tasks/pi_ingest.py).

    Stamps event.processed_at on completion and event.error (never raises)
    on failure, so a periodic sweep can retry only the events that actually
    failed rather than reprocessing everything. Caller controls the
    transaction (this only flushes, never commits) — same contract as
    pi_ingest.write_capture_event.
    """
    stats = {
        "screens_new": 0, "screens_seen": 0,
        "components_new": 0, "components_seen": 0,
        "edges_new": 0, "edges_seen": 0,
        "behavior_notes_new": 0,
    }
    if not pi_ingest.pi_enabled() or event is None:
        return stats

    from app.services import pi_drift  # lazy: pi_drift imports this module back (see pi_drift.py)

    try:
        payload = event.payload_json or {}
        observations = payload.get("screens") or []
        environment_id = payload.get("environment_id") or None

        prev_screen_row = None
        prev_last_action_label: Optional[str] = None

        for obs in observations:
            if not isinstance(obs, dict):
                continue
            route = obs.get("route")
            if not route:
                continue

            screen_row, created = _resolve_screen(
                db, project_id=event.project_id, environment_id=environment_id,
                route=route, title=obs.get("title"), source_type=event.source_type,
            )
            stats["screens_new" if created else "screens_seen"] += 1

            actions = obs.get("actions") or []
            observed_component_ids: set = set()
            newly_created_tier5: list = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                comp_row, comp_created = _resolve_component(
                    db, project_id=event.project_id, screen_row=screen_row, action=action,
                )
                if comp_row is None:
                    continue
                stats["components_new" if comp_created else "components_seen"] += 1
                observed_component_ids.add(comp_row.id)

                # Phase 2 (spec §18) — classification only, never mutates
                # identity. Runs after _resolve_component so it sees the
                # row exactly as entity resolution left it.
                if comp_created:
                    pi_drift.classify_added(db, project_id=event.project_id, component_row=comp_row)
                    if comp_row.identity_tier == 5:
                        newly_created_tier5.append(comp_row)
                else:
                    pi_drift.classify_reobservation(
                        db, project_id=event.project_id, screen_row=screen_row,
                        component_row=comp_row, action=action,
                    )

            # Removed / candidate_rename are properties of the WHOLE visit
            # (what was and wasn't seen this time), not of one action, so
            # they run once per screen visit rather than per action.
            freshly_vanished_tier5 = pi_drift.reconcile_screen_visit(
                db, project_id=event.project_id, screen_row=screen_row,
                observed_component_ids=observed_component_ids,
            )
            pi_drift.maybe_pair_candidate_renames(
                db, project_id=event.project_id, screen_row=screen_row,
                newly_created_tier5=newly_created_tier5,
                freshly_vanished_tier5=freshly_vanished_tier5,
            )

            if prev_screen_row is not None and prev_screen_row.id != screen_row.id:
                _edge, edge_created = _resolve_edge(
                    db, project_id=event.project_id,
                    from_screen_id=prev_screen_row.id, to_screen_id=screen_row.id,
                    trigger_action=prev_last_action_label,
                )
                stats["edges_new" if edge_created else "edges_seen"] += 1

            if created:
                note = _maybe_write_behavior_note(
                    db, project_id=event.project_id, screen_row=screen_row, actions=actions,
                    source_type=event.source_type, source_ref=str(event.id),
                )
                if note is not None:
                    stats["behavior_notes_new"] += 1

            prev_screen_row = screen_row
            prev_last_action_label = (
                str(actions[-1].get("label")) if actions and isinstance(actions[-1], dict)
                and actions[-1].get("label") else None
            )

        event.processed_at = datetime.utcnow()
        event.error = None
        db.flush()
        return stats
    except Exception as exc:  # noqa: BLE001 — auxiliary to whatever produced the capture
        logger.exception(
            "pi_extract: failed to process capture event %s (project %s)",
            getattr(event, "id", None), getattr(event, "project_id", None),
        )
        try:
            event.error = str(exc)[:2000]
            db.flush()
        except Exception:
            pass
        return stats


def process_pending(db, *, limit: int = 50) -> dict[str, Any]:
    """Sweep unprocessed pi_capture_events (processed_at IS NULL) and extract
    each one, committing per event so one bad row cannot roll back the rest
    of the batch. Intended for a periodic Celery beat task (see
    workers/tasks/pi_ingest.py) as a retry net for events whose synchronous
    extraction failed transiently — the normal path extracts immediately
    after write_capture_event in the same request/task.
    """
    from app.models.project_intelligence import PiCaptureEvent

    if not pi_ingest.pi_enabled():
        return {"processed": 0}

    processed = 0
    try:
        events = (
            db.query(PiCaptureEvent)
            .filter(PiCaptureEvent.processed_at.is_(None))
            .order_by(PiCaptureEvent.created_at.asc())
            .limit(limit)
            .all()
        )
        for event in events:
            process_capture_event(db, event)
            try:
                db.commit()
            except Exception:
                logger.exception("pi_extract: commit failed for capture event %s", event.id)
                db.rollback()
                continue
            processed += 1
    except Exception:
        logger.exception("pi_extract: process_pending sweep failed")
    return {"processed": processed}
