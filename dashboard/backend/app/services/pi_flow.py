"""Project Intelligence — Flow model service.

The one module that talks directly to pi_flows, the table
app/services/flow_validation.get_flow_model() reads (see that module's
"THIS IS THE SEAM" comment). get_verified_model() below is the function the
seam's replacement body calls — see task #12 — so its contract is fixed by
flow_validation.py's own docstring: return a dict shaped like

    {"entry_state": "S00_...", "states": [
        {"id": str, "name": str|None, "requires": [str], "pages": [str],
         "locked_behaviours": [str]}
    ]}

or None, and NEVER raise.

DETERMINISTIC-FIRST (spec §18.1 / §17). The graph structure — which states
exist, and which states require which — is built entirely from
pi_screens/pi_navigation_edges by a topological sort; nothing about that
structure is proposed or alterable by an LLM call. The one LLM call in
propose_model() is asked ONLY to write a human-readable `name` per state; it
is never given the graph and cannot change it. `locked_behaviours` is never
machine-written anywhere in this module — flow_validation.py's own docstring
explains why (no observation can prove a behaviour is *permanently*
unavailable, and a wrong lock silently flags correct checkpoints as
violations). It is populated only by a human, through approve_flow()'s
`edit` argument.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from app.core.logging import get_logger
from app.services import pi_ingest

logger = get_logger(__name__)

_MAX_STATES_PER_MODEL = 200


# ── Deterministic graph construction ─────────────────────────────────────

def _slug_from_route(route: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", route or "")
    slug = "_".join(t.upper() for t in tokens[:4]) or "SCREEN"
    return slug[:40]


def _build_deterministic_states(db, *, project_id, environment_id) -> list[dict[str, Any]]:
    """One state per active screen, topologically ordered by observed
    navigation edges (Kahn's algorithm). A screen with several incoming
    edges picks the highest-observed_count one as its `requires` parent —
    the graph a reviewer sees is the single most-travelled path, not every
    path ever taken, which is what keeps the proposed model readable.

    Returns [] if there are no screens yet (propose_model treats that as
    "nothing to propose" and never writes a row for it).
    """
    from app.models.project_intelligence import PiScreen, PiNavigationEdge, PiStatus

    screens = (
        db.query(PiScreen)
        .filter(
            PiScreen.project_id == project_id,
            PiScreen.status.in_((PiStatus.pending, PiStatus.verified)),
        )
        .order_by(PiScreen.first_seen_at.asc())
        .limit(_MAX_STATES_PER_MODEL)
        .all()
    )
    if not screens:
        return []

    edges = (
        db.query(PiNavigationEdge)
        .filter(
            PiNavigationEdge.project_id == project_id,
            PiNavigationEdge.status.in_((PiStatus.pending, PiStatus.verified)),
            PiNavigationEdge.from_screen_id.in_([s.id for s in screens]),
            PiNavigationEdge.to_screen_id.in_([s.id for s in screens]),
        )
        .all()
    )

    incoming: dict[Any, list] = {}
    for edge in edges:
        incoming.setdefault(edge.to_screen_id, []).append(edge)

    # Kahn's algorithm over screen ids, tie-broken by first_seen_at (the
    # `screens` query's own order) so the sort is stable across runs.
    in_degree = {s.id: len(incoming.get(s.id, [])) for s in screens}
    by_id = {s.id: s for s in screens}
    order_hint = {s.id: i for i, s in enumerate(screens)}

    ready = sorted([sid for sid, d in in_degree.items() if d == 0], key=lambda sid: order_hint[sid])
    visited: list = []
    remaining_edges = list(edges)

    def _outgoing(sid):
        return [e for e in remaining_edges if e.from_screen_id == sid]

    seen_ids = set()
    while ready:
        sid = ready.pop(0)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        visited.append(sid)
        for edge in _outgoing(sid):
            in_degree[edge.to_screen_id] -= 1
            if in_degree[edge.to_screen_id] <= 0 and edge.to_screen_id not in seen_ids:
                ready.append(edge.to_screen_id)
        ready.sort(key=lambda x: order_hint[x])

    # A cycle (or an edge from/into a screen not in `screens`) can leave
    # nodes unvisited — append them in their original order rather than
    # dropping them, so nothing observed silently disappears from the
    # proposal; the reviewer sees a state with no requires, which is a
    # legitimate (if unideal) starting point, not a crash.
    for s in screens:
        if s.id not in seen_ids:
            visited.append(s.id)
            seen_ids.add(s.id)

    state_id_by_screen = {sid: f"S{i:02d}_{_slug_from_route(by_id[sid].route)}"
                           for i, sid in enumerate(visited)}

    states: list[dict[str, Any]] = []
    for sid in visited:
        screen = by_id[sid]
        candidates = incoming.get(sid, [])
        requires: list[str] = []
        if candidates:
            best = max(candidates, key=lambda e: (e.observed_count or 0))
            if best.from_screen_id in state_id_by_screen and best.from_screen_id != sid:
                requires = [state_id_by_screen[best.from_screen_id]]
        states.append({
            "id": state_id_by_screen[sid],
            "name": None,  # filled by the LLM naming pass, best-effort
            "requires": requires,
            "pages": [screen.route],
            "locked_behaviours": [],  # human-authored only — see module docstring
            "_screen_id": str(sid),
            "_route": screen.route,
            "_title": screen.title,
        })
    return states


# ── LLM naming pass (the one non-deterministic step) ─────────────────────

_NAMING_SYSTEM = (
    "You give short, human-readable names to states in a product's execution "
    "flow. You are given an ordered list of states, each with the page "
    "route(s) it covers and, when known, that page's title. Do not invent "
    "new states, do not reorder them, do not change their ids — only supply "
    "a `name`: 3-6 words describing what has just become true for a user "
    'who has reached this state (e.g. "Authenticated", "Workspace created", '
    '"Skills extracted from job description"). '
    'Respond with JSON only: {"names": {"<state id>": "<name>", ...}}.'
)


def _apply_llm_names(states: list[dict[str, Any]]) -> None:
    """Best-effort, in place. A failed/empty/malformed response leaves every
    state's `name` as None — flow_validation.py never reads `name` (only
    `id`/`requires`/`pages`/`locked_behaviours`), so this is cosmetic only
    and never blocks the proposal."""
    if not states:
        return
    try:
        from app.services import llm_router

        lines = [
            f"{s['id']}: pages={s['_route']}" + (f", title={s['_title']}" if s.get("_title") else "")
            for s in states
        ]
        prompt = "States, in order:\n" + "\n".join(lines)
        result = llm_router.complete_json_complete(prompt, system=_NAMING_SYSTEM, max_tokens=2048)
        names = (result.parsed_json or {}).get("names") or {}
        if not isinstance(names, dict):
            return
        for s in states:
            name = names.get(s["id"])
            if isinstance(name, str) and name.strip():
                s["name"] = name.strip()[:200]
    except Exception:  # noqa: BLE001 — a name is cosmetic, never required
        logger.warning("pi_flow: state-naming pass failed — proposal proceeds without names",
                        exc_info=True)


def _entry_state_id(states: list[dict[str, Any]]) -> Optional[str]:
    roots = [s["id"] for s in states if not s["requires"]]
    if len(roots) == 1:
        return roots[0]
    # Several/zero roots: fall back to the first state in the deterministic
    # (topological / first_seen) order — always exactly one candidate, so
    # the model this writes always has a resolvable entry_state and never
    # hits flow_validation.build_index's "ambiguous roots -> None" case.
    return states[0]["id"] if states else None


def _clean_for_storage(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in s.items() if not k.startswith("_")}
        for s in states
    ]


# ── Public API ────────────────────────────────────────────────────────────

def propose_model(db, *, project_id, environment_id=None,
                   generated_from_run_ids: Optional[list[str]] = None):
    """Build a new pending pi_flows row from the current pi_screens /
    pi_navigation_edges graph, and store it (so it surfaces in the review
    queue like everything else — a flow proposal is reviewed the same way
    a screen or component is, per spec §17.3/§22). Returns the new PiFlow
    row, or None if there is nothing to propose yet or the write failed.

    Never raises: this is called from a worker task after ingestion, and a
    failed proposal must not fail the run that triggered it.
    """
    from app.models.project_intelligence import PiFlow, PiStatus

    if not pi_ingest.pi_enabled():
        return None
    try:
        states = _build_deterministic_states(db, project_id=project_id, environment_id=environment_id)
        if not states:
            return None

        _apply_llm_names(states)
        entry_state = _entry_state_id(states)
        model_json = {"entry_state": entry_state, "states": _clean_for_storage(states)}

        version = next_version(db, project_id=project_id, environment_id=environment_id)
        row = PiFlow(
            project_id=project_id,
            environment_id=environment_id,
            version=version,
            model_json=model_json,
            status=PiStatus.pending,
            generated_from_run_ids=generated_from_run_ids,
        )
        db.add(row)
        db.flush()
        return row
    except Exception:
        logger.exception("pi_flow: propose_model failed for project %s", project_id)
        return None


def next_version(db, *, project_id, environment_id) -> int:
    """The version number a new pi_flows row for this (project, environment)
    pair should use next. Public because the API layer needs it too, for a
    human-authored PiFlowCreate submission that bypasses propose_model's
    LLM naming pass entirely (see api/v1/project_intelligence.py)."""
    from app.models.project_intelligence import PiFlow

    latest = (
        db.query(PiFlow)
        .filter(PiFlow.project_id == project_id, PiFlow.environment_id == environment_id)
        .order_by(PiFlow.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def approve_flow(db, *, flow_id, actor_user_id=None, edit: Optional[dict[str, Any]] = None):
    """Verify a pending pi_flows row, superseding whatever was previously
    verified for the same (project_id, environment_id) pair — the same
    supersede-not-overwrite pattern SowRequirementsLedger uses (spec §17.3).

    `edit`: an already-validated dict matching schemas.PiFlowModelIn (the
    API layer is responsible for that validation — this function trusts its
    caller and only applies it), replacing model_json before verifying.
    This is the ONLY path locked_behaviours can be populated through.

    Returns the (now verified) PiFlow row, or None if flow_id does not
    resolve to a pending row. Raises only on a genuine DB error — unlike
    the ingestion/extraction paths, an explicit human approval action
    failing IS something the caller (an API request) must surface, not
    silently swallow.
    """
    from app.models.project_intelligence import PiFlow, PiStatus, PiChangeLog

    row = db.query(PiFlow).filter(PiFlow.id == flow_id).one_or_none()
    if row is None or row.status != PiStatus.pending:
        return None

    previous_value = None
    if edit:
        previous_value = row.model_json
        row.model_json = edit
        row.edited_by = actor_user_id

    current_verified = (
        db.query(PiFlow)
        .filter(
            PiFlow.project_id == row.project_id,
            PiFlow.environment_id == row.environment_id,
            PiFlow.status == PiStatus.verified,
        )
        .one_or_none()
    )
    if current_verified is not None:
        current_verified.status = PiStatus.superseded
        current_verified.superseded_by_id = row.id

    row.status = PiStatus.verified

    db.add(PiChangeLog(
        project_id=row.project_id,
        entity_type="flow",
        entity_id=row.id,
        change_type="edited" if edit else "added",
        previous_value=previous_value,
        new_value=row.model_json,
    ))
    db.flush()
    return row


def reject_flow(db, *, flow_id, reason: Optional[str] = None):
    """Mark a pending pi_flows row rejected. Never verifies anything else —
    the previously-verified model (if any) is untouched and stays in force,
    which is the correct fail-safe: rejecting a bad proposal must not leave
    the project with no flow model at all."""
    from app.models.project_intelligence import PiFlow, PiStatus, PiChangeLog

    row = db.query(PiFlow).filter(PiFlow.id == flow_id).one_or_none()
    if row is None or row.status != PiStatus.pending:
        return None

    row.status = PiStatus.rejected
    db.add(PiChangeLog(
        project_id=row.project_id,
        entity_type="flow",
        entity_id=row.id,
        change_type="removed",
        previous_value=row.model_json,
        new_value=None,
    ))
    db.flush()
    return row


def get_verified_model(db, project_id, environment_id=None) -> Optional[dict]:
    """The function flow_validation.get_flow_model()'s new body calls
    (task #12). Returns the verified pi_flows.model_json for this project,
    or None. Never raises — a DB error here must degrade to "no flow model"
    exactly like a missing TDD_FLOW_MODEL_PATH file does today, never take
    down whatever pipeline asked for the flow model.

    environment_id is accepted for forward compatibility with a later,
    environment-scoped seam call, but Phase 1's flow_validation.get_flow_model
    signature is (session, project_id) only — when omitted, this prefers the
    project-wide (environment_id IS NULL) verified row and falls back to the
    most recently updated verified row for any environment, since the
    partial unique index guarantees at most one verified row per
    (project_id, environment_id) pair, never more than one candidate to
    choose between per environment.
    """
    if project_id is None or not pi_ingest.pi_enabled():
        return None
    try:
        from app.models.project_intelligence import PiFlow, PiStatus

        query = db.query(PiFlow).filter(
            PiFlow.project_id == project_id, PiFlow.status == PiStatus.verified,
        )
        if environment_id is not None:
            query = query.filter(PiFlow.environment_id == environment_id)
            row = query.order_by(PiFlow.updated_at.desc()).first()
            if row is not None:
                return row.model_json

        row = (
            db.query(PiFlow)
            .filter(PiFlow.project_id == project_id, PiFlow.status == PiStatus.verified)
            .order_by(PiFlow.environment_id.is_(None).desc(), PiFlow.updated_at.desc())
            .first()
        )
        return row.model_json if row is not None else None
    except Exception:
        logger.exception("pi_flow: get_verified_model failed for project %s", project_id)
        return None
