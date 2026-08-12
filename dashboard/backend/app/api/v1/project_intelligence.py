"""Project Intelligence API — screens, components, behaviour notes, flow
models, and the unified review queue.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) and
app/services/pi_extract.py / pi_flow.py for how these rows are produced.
This file is read-only orchestration on top of that: every endpoint either
lists/reads what ingestion+extraction already wrote, or applies a human
review decision (approve/edit/reject) to one pending row.

Feature-flagged behind PI_ENABLED (default: off), same convention as
SOW_ENABLED in app/api/v1/sow.py — every endpoint 404s until explicitly
enabled, so existing deployments see zero behavior change from this file
existing.

Access control: "project_intelligence" (read/browse — the AI Intelligence
tab itself) vs "project_intelligence_review" (approve/edit/reject a pending
record) — see app/core/permissions.py for why these are kept distinct.
project_id is a convenience filter only, not an access boundary — same
posture as every other project-scoped resource in this codebase (see
app/api/v1/sow.py's module docstring, which this mirrors).

PHASE 2 (Change Detection & Healing, spec §18-19): the drift-flags
endpoints at the bottom of this file are deliberately NOT part of the
generic review-queue/review-action machinery above. Ledger healing is a
dedicated, one-at-a-time, diff-visible operation with its own kill switch
(PI_HEAL_LEDGER) — spec §19.4 explicitly rules out a bulk path for it.
Every write those endpoints make is delegated to services/pi_heal.py,
which is the only code in this entire feature that ever touches
sow_requirements_ledger.
"""
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.project_intelligence import (
    PiBehaviorNote,
    PiComponent,
    PiDesignPattern,
    PiDriftFlag,
    PiFlow,
    PiNavigationEdge,
    PiReviewAction,
    PiScreen,
    PiStatus,
)
from app.models.user import User
from app.schemas.project_intelligence import (
    PiBehaviorNoteOut,
    PiBehaviorNoteUpdate,
    PiComponentOut,
    PiComponentUpdate,
    PiContextEffectivenessOut,
    PiDesignPatternOut,
    PiDesignPatternUpdate,
    PiDriftFlagApplyIn,
    PiDriftFlagOut,
    PiDriftFlagRejectIn,
    PiFlowCreate,
    PiFlowOut,
    PiNavigationEdgeOut,
    PiQueueItemOut,
    PiReviewActionIn,
    PiReviewActionOut,
    PiScreenOut,
    PiScreenUpdate,
)
from app.services import pi_flow as pi_flow_service
from app.services import pi_heal as pi_heal_service
from app.services import pi_ingest as pi_ingest_service
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/project-intelligence", tags=["project-intelligence"])

_ENTITY_TYPES = ("screen", "component", "behavior_note", "flow", "design_pattern")
_SIMPLE_ENTITY_MODELS = {
    "screen": (PiScreen, PiScreenUpdate),
    "component": (PiComponent, PiComponentUpdate),
    "behavior_note": (PiBehaviorNote, PiBehaviorNoteUpdate),
    # Phase 3 (spec §14.3/table 8) — a design pattern carries no high-risk
    # write (nothing outside pi_design_patterns is ever touched by
    # approving/editing/rejecting one), so unlike drift-flag healing it is
    # folded straight into the generic review-action machinery rather than
    # getting its own dedicated endpoints.
    "design_pattern": (PiDesignPattern, PiDesignPatternUpdate),
}


def _feature_enabled() -> None:
    """Gate every endpoint behind PI_ENABLED (default: off), matching the
    SOW_ENABLED / VISUAL_AUDIT_ENABLED precedent."""
    if os.environ.get("PI_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Project Intelligence is not enabled")


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _project_scope(query, model, project_id):
    if project_id is not None:
        query = query.filter(model.project_id == project_id)
    return query


# ── Screens ───────────────────────────────────────────────────────────────

@router.get("/screens", response_model=list[PiScreenOut])
def list_screens(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    _feature_enabled()
    query = _project_scope(db.query(PiScreen), PiScreen, project_id)
    if status_filter:
        query = query.filter(PiScreen.status == status_filter)
    rows = query.order_by(PiScreen.last_seen_at.desc()).limit(limit).all()
    return rows


@router.patch("/screens/{screen_id}", response_model=PiScreenOut)
def update_screen(
    screen_id: uuid.UUID,
    payload: PiScreenUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Content-only correction — never changes status. Use the review-action
    endpoint below to approve/reject, or to edit-and-verify in one step."""
    _feature_enabled()
    row = db.get(PiScreen, screen_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


# ── Navigation edges (read-only — no independent review workflow; an edge
#    is corroborating detail for the flow model review, not reviewed alone) ─

@router.get("/navigation-edges", response_model=list[PiNavigationEdgeOut])
def list_navigation_edges(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _feature_enabled()
    query = _project_scope(db.query(PiNavigationEdge), PiNavigationEdge, project_id)
    rows = query.order_by(PiNavigationEdge.observed_count.desc()).limit(limit).all()
    return rows


# ── Components ───────────────────────────────────────────────────────────

@router.get("/components", response_model=list[PiComponentOut])
def list_components(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    screen_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _feature_enabled()
    query = _project_scope(db.query(PiComponent), PiComponent, project_id)
    if screen_id is not None:
        query = query.filter(PiComponent.screen_id == screen_id)
    if status_filter:
        query = query.filter(PiComponent.status == status_filter)
    rows = query.order_by(PiComponent.last_seen_at.desc()).limit(limit).all()
    return rows


@router.patch("/components/{component_id}", response_model=PiComponentOut)
def update_component(
    component_id: uuid.UUID,
    payload: PiComponentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    _feature_enabled()
    row = db.get(PiComponent, component_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Component not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


# ── Behaviour notes ──────────────────────────────────────────────────────

@router.get("/behavior-notes", response_model=list[PiBehaviorNoteOut])
def list_behavior_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    screen_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    _feature_enabled()
    query = _project_scope(db.query(PiBehaviorNote), PiBehaviorNote, project_id)
    if screen_id is not None:
        query = query.filter(PiBehaviorNote.screen_id == screen_id)
    rows = query.order_by(PiBehaviorNote.created_at.desc()).limit(limit).all()
    return rows


@router.get("/behavior-notes/search", response_model=list[PiBehaviorNoteOut])
def search_behavior_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID = Query(...),
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Semantic (cosine-similarity) search over verified behavior notes —
    Phase 5, spec §16 table 8 / table 17. Declared before the
    /behavior-notes/{note_id} PATCH route's path shape to avoid any future
    ambiguity if a GET is ever added there.

    Falls back to the plain list endpoint's ILIKE-style behavior (i.e.
    returns [] here) when PI_SEMANTIC_SEARCH_ENABLED is off, pgvector
    isn't installed, or the embedding call fails — see
    services/pi_embed.py:semantic_search_behavior_notes()'s own docstring.
    The frontend falls back to the existing list+client-filter behaviour
    on an empty/failed response, so a transient embedding-API outage
    degrades search quality, never search availability.
    """
    _feature_enabled()
    from app.services import pi_embed

    rows = pi_embed.semantic_search_behavior_notes(
        db, project_id=project_id, query_text=q, limit=limit,
    )
    return rows


@router.patch("/behavior-notes/{note_id}", response_model=PiBehaviorNoteOut)
def update_behavior_note(
    note_id: uuid.UUID,
    payload: PiBehaviorNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    _feature_enabled()
    row = db.get(PiBehaviorNote, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Behavior note not found")
    row.description = payload.description
    db.commit()
    db.refresh(row)
    return row


# ── Design patterns (Phase 3 — Active Crawler & Visual, spec §14.3/table 8) ──
# Browsing/listing is the "Design Library" view (table 17); approve/edit/
# reject reuses apply_review_action below via _SIMPLE_ENTITY_MODELS, same
# as screens/components/behaviour notes.

def _denormalize_design_patterns(db: Session, rows: list[PiDesignPattern]) -> list[PiDesignPatternOut]:
    if not rows:
        return []
    screen_ids = {r.screen_id for r in rows if r.screen_id}
    screens_by_id = {
        s.id: s for s in db.query(PiScreen).filter(PiScreen.id.in_(screen_ids)).all()
    } if screen_ids else {}
    return [
        PiDesignPatternOut(
            id=r.id, project_id=r.project_id, screen_id=r.screen_id,
            pattern_type=r.pattern_type, value=r.value, description=r.description,
            evidence_ref=r.evidence_ref, confidence=r.confidence, status=r.status.value,
            created_at=r.created_at, updated_at=r.updated_at,
            screen_route=(screens_by_id.get(r.screen_id).route if r.screen_id in screens_by_id else None),
        )
        for r in rows
    ]


@router.get("/design-patterns", response_model=list[PiDesignPatternOut])
def list_design_patterns(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    pattern_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    _feature_enabled()
    query = _project_scope(db.query(PiDesignPattern), PiDesignPattern, project_id)
    if pattern_type:
        query = query.filter(PiDesignPattern.pattern_type == pattern_type)
    if status_filter:
        query = query.filter(PiDesignPattern.status == status_filter)
    rows = query.order_by(PiDesignPattern.created_at.desc()).limit(limit).all()
    return _denormalize_design_patterns(db, rows)


@router.get("/design-patterns/{pattern_id}/screenshot")
def get_design_pattern_screenshot(
    pattern_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
):
    """Stream the crawl screenshot a design pattern was read from. Mirrors
    api/v1/visual_audit.py's get_run_image: the path comes from OUR
    database row (server-generated by services/pi_crawl.py), never client
    input, so there is no path-traversal surface here. 404s once the
    retention cleanup task has cleared evidence_ref (the file aged out) —
    the pattern itself is still visible everywhere else, only its image
    preview goes away."""
    _feature_enabled()
    row = db.get(PiDesignPattern, pattern_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Design pattern not found")
    if not row.evidence_ref or not os.path.exists(row.evidence_ref):
        raise HTTPException(status_code=404, detail="Screenshot not available (may have expired)")
    return FileResponse(row.evidence_ref, media_type="image/png")


@router.patch("/design-patterns/{pattern_id}", response_model=PiDesignPatternOut)
def update_design_pattern(
    pattern_id: uuid.UUID,
    payload: PiDesignPatternUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Content-only correction — never changes status. Use the review-action
    endpoint below to approve/reject, or to edit-and-verify in one step."""
    _feature_enabled()
    row = db.get(PiDesignPattern, pattern_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Design pattern not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _denormalize_design_patterns(db, [row])[0]


# ── Flow models ──────────────────────────────────────────────────────────

@router.get("/flows", response_model=list[PiFlowOut])
def list_flows(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
):
    _feature_enabled()
    query = _project_scope(db.query(PiFlow), PiFlow, project_id)
    if status_filter:
        query = query.filter(PiFlow.status == status_filter)
    rows = query.order_by(PiFlow.version.desc()).limit(limit).all()
    return rows


@router.get("/flows/{flow_id}", response_model=PiFlowOut)
def get_flow(
    flow_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
):
    _feature_enabled()
    row = db.get(PiFlow, flow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return row


@router.post("/flows", response_model=PiFlowOut, status_code=status.HTTP_201_CREATED)
def create_flow(
    payload: PiFlowCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """A human authoring or correcting a flow model from scratch. Always
    creates a new pending version (never mutates a verified row in place —
    spec §17.3) so it goes through the same review-action approval as a
    machine-proposed one, keeping one audit trail for both origins."""
    _feature_enabled()
    version = pi_flow_service.next_version(
        db, project_id=payload.project_id, environment_id=payload.environment_id
    )
    row = PiFlow(
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        version=version,
        model_json=payload.model.model_dump(),
        status=PiStatus.pending,
        edited_by=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    write_audit_log(
        db, user_id=current_user.id, action="create_pi_flow",
        resource_type="pi_flow", resource_id=str(row.id),
        details={"project_id": str(payload.project_id), "version": version},
        ip_address=_client_ip(request),
    )
    return row


# ── Unified review queue ─────────────────────────────────────────────────

def _screen_summary(row: PiScreen) -> str:
    return f"New screen: {row.route}"


def _component_summary(row: PiComponent, screen: PiScreen | None) -> str:
    where = f" on {screen.route}" if screen else ""
    return f"New/changed control: \"{row.label}\"{where}"


def _behavior_note_summary(row: PiBehaviorNote, screen: PiScreen | None) -> str:
    where = f" on {screen.route}" if screen else ""
    text = row.description if len(row.description) <= 100 else row.description[:97] + "..."
    return f"Behavior note{where}: {text}"


def _flow_summary(row: PiFlow) -> str:
    n_states = len((row.model_json or {}).get("states") or [])
    return f"Flow model v{row.version} ({n_states} state(s))"


def _design_pattern_summary(row: PiDesignPattern) -> str:
    return f"Design pattern ({row.pattern_type}): {row.description or 'no description'}"


@router.get("/review-queue", response_model=list[PiQueueItemOut])
def get_review_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Assembled server-side across the four pending-row tables, oldest
    first (spec §21.3). Not a DB row — a projection, so pagination here is
    a simple per-table cap rather than a real cross-table OFFSET; that's an
    acceptable tradeoff for a queue that is expected to stay small (Phase 1
    has no rename-detection yet generating a steady stream of drift flags —
    see the module docstring)."""
    _feature_enabled()

    items: list[PiQueueItemOut] = []

    screens = _project_scope(
        db.query(PiScreen).filter(PiScreen.status == PiStatus.pending), PiScreen, project_id,
    ).order_by(PiScreen.first_seen_at.asc()).limit(limit).all()
    for s in screens:
        items.append(PiQueueItemOut(
            entity_type="screen", entity_id=s.id, project_id=s.project_id,
            summary=_screen_summary(s), status=s.status.value, bulk_eligible=True,
            submitted_at=s.first_seen_at, detail={"route": s.route, "title": s.title},
        ))

    components = _project_scope(
        db.query(PiComponent).filter(PiComponent.status == PiStatus.pending), PiComponent, project_id,
    ).order_by(PiComponent.created_at.asc()).limit(limit).all()
    screen_ids = {c.screen_id for c in components}
    screens_by_id = {
        sc.id: sc for sc in db.query(PiScreen).filter(PiScreen.id.in_(screen_ids)).all()
    } if screen_ids else {}
    for c in components:
        screen = screens_by_id.get(c.screen_id)
        items.append(PiQueueItemOut(
            entity_type="component", entity_id=c.id, project_id=c.project_id,
            summary=_component_summary(c, screen), status=c.status.value, bulk_eligible=True,
            submitted_at=c.created_at,
            detail={"label": c.label, "identity_tier": c.identity_tier,
                    "route": screen.route if screen else None},
        ))

    notes = _project_scope(
        db.query(PiBehaviorNote).filter(PiBehaviorNote.status == PiStatus.pending), PiBehaviorNote, project_id,
    ).order_by(PiBehaviorNote.created_at.asc()).limit(limit).all()
    note_screen_ids = {n.screen_id for n in notes}
    note_screens_by_id = {
        sc.id: sc for sc in db.query(PiScreen).filter(PiScreen.id.in_(note_screen_ids)).all()
    } if note_screen_ids else {}
    for n in notes:
        screen = note_screens_by_id.get(n.screen_id)
        items.append(PiQueueItemOut(
            entity_type="behavior_note", entity_id=n.id, project_id=n.project_id,
            summary=_behavior_note_summary(n, screen), status=n.status.value, bulk_eligible=True,
            submitted_at=n.created_at,
            detail={"description": n.description, "confidence": n.confidence},
        ))

    flows = _project_scope(
        db.query(PiFlow).filter(PiFlow.status == PiStatus.pending), PiFlow, project_id,
    ).order_by(PiFlow.created_at.asc()).limit(limit).all()
    for f in flows:
        items.append(PiQueueItemOut(
            entity_type="flow", entity_id=f.id, project_id=f.project_id,
            summary=_flow_summary(f), status=f.status.value, bulk_eligible=False,
            submitted_at=f.created_at,
            detail={"version": f.version, "model_json": f.model_json},
        ))

    patterns = _project_scope(
        db.query(PiDesignPattern).filter(PiDesignPattern.status == PiStatus.pending),
        PiDesignPattern, project_id,
    ).order_by(PiDesignPattern.created_at.asc()).limit(limit).all()
    for p in patterns:
        items.append(PiQueueItemOut(
            entity_type="design_pattern", entity_id=p.id, project_id=p.project_id,
            summary=_design_pattern_summary(p), status=p.status.value, bulk_eligible=True,
            submitted_at=p.created_at,
            detail={"pattern_type": p.pattern_type, "value": p.value,
                    "evidence_ref": p.evidence_ref},
        ))

    items.sort(key=lambda i: i.submitted_at)
    return items[:limit]


# ── Review actions ───────────────────────────────────────────────────────

def _apply_simple_review_action(db, *, entity_type: str, entity_id, action: str,
                                 edit: dict | None):
    model_cls, update_schema = _SIMPLE_ENTITY_MODELS[entity_type]
    row = db.query(model_cls).filter(model_cls.id == entity_id).one_or_none()
    if row is None or row.status != PiStatus.pending:
        return None

    if action == "reject":
        row.status = PiStatus.rejected
        db.flush()
        return row

    if action == "edit":
        if not edit:
            raise HTTPException(status_code=422, detail="edit action requires an 'edit' payload")
        try:
            validated = update_schema(**edit)
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a 422
            raise HTTPException(status_code=422, detail=f"Invalid edit payload: {exc}") from exc
        for field, value in validated.model_dump(exclude_unset=True).items():
            setattr(row, field, value)

    row.status = PiStatus.verified
    db.flush()
    return row


@router.post(
    "/{entity_type}/{entity_id}/review-action",
    response_model=PiReviewActionOut,
    status_code=status.HTTP_201_CREATED,
)
def apply_review_action(
    entity_type: str,
    entity_id: uuid.UUID,
    action_in: PiReviewActionIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Approve, edit-and-verify, or reject one pending row of any Project
    Intelligence entity type. One endpoint for all four so the frontend's
    review queue (spec §21.3/§22) has a single action shape regardless of
    which table a queue item came from.

    `action_in.edit`'s shape depends on entity_type: validated against
    PiScreenUpdate/PiComponentUpdate/PiBehaviorNoteUpdate inside
    _apply_simple_review_action, or trusted as PiFlowModelIn-shaped JSON for
    a flow — pi_flow.approve_flow stores it as-is (a flow's model_json has
    no separate typed Update schema; PiFlowModelIn already fully describes
    it, and re-validating a second time here would just duplicate that).
    """
    _feature_enabled()
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown entity_type '{entity_type}'")

    if action_in.action == "reject" and not (action_in.reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required to reject")

    if entity_type == "flow":
        if action_in.action == "approve":
            row = pi_flow_service.approve_flow(
                db, flow_id=entity_id, actor_user_id=current_user.id, edit=None,
            )
        elif action_in.action == "edit":
            row = pi_flow_service.approve_flow(
                db, flow_id=entity_id, actor_user_id=current_user.id, edit=action_in.edit,
            )
        else:
            row = pi_flow_service.reject_flow(db, flow_id=entity_id, reason=action_in.reason)
        if row is None:
            raise HTTPException(status_code=404, detail="Flow not found or not pending")
        project_id = row.project_id
    else:
        row = _apply_simple_review_action(
            db, entity_type=entity_type, entity_id=entity_id,
            action=action_in.action, edit=action_in.edit,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"{entity_type} not found or not pending")
        project_id = row.project_id

    review_action = PiReviewAction(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action_in.action,
        reason=action_in.reason,
        actor_user_id=current_user.id,
    )
    db.add(review_action)
    db.commit()
    db.refresh(review_action)

    write_audit_log(
        db, user_id=current_user.id, action=f"pi_review_{action_in.action}",
        resource_type=f"pi_{entity_type}", resource_id=str(entity_id),
        details={"reason": action_in.reason} if action_in.reason else None,
        ip_address=_client_ip(request),
    )
    logger.info(
        "Project Intelligence: %s %sd entity %s (%s) for project %s",
        current_user.id, action_in.action, entity_id, entity_type, project_id,
    )

    # Phase 5 (Scale) — fire-and-forget re-embed. Queued after the review
    # action is already committed, never awaited: a queueing failure or a
    # failure inside the embed task itself must never affect the review
    # decision that triggered it. Only approve/edit ever leave a behavior
    # note at status='verified' (reject does not — see
    # _apply_simple_review_action above), and semantic_search_enabled()
    # inside the task itself is the real gate — this call is cheap and
    # harmless to make unconditionally.
    if entity_type == "behavior_note" and action_in.action in ("approve", "edit"):
        try:
            from app.workers.tasks.pi_embed import embed_one_note

            embed_one_note.delay(str(entity_id))
        except Exception:
            logger.warning(
                "Project Intelligence: could not queue embedding for behavior note %s",
                entity_id, exc_info=True,
            )

    return review_action


# ── Drift flags (Phase 2 — Change Detection & Healing, spec §18-19) ─────────
# Dedicated endpoints, not routed through apply_review_action above — see
# this file's module docstring and services/pi_heal.py's module docstring
# for why ledger healing gets its own non-bulk operation.

def _denormalize_drift_flags(db: Session, flags: list[PiDriftFlag]) -> list[PiDriftFlagOut]:
    """Batch-fetch every referenced screen/component/ledger row once (same
    N+1-avoidance pattern get_review_queue already uses) and assemble the
    diff-view fields the frontend needs."""
    from app.models.sow import SowRequirementsLedger

    heal_enabled = pi_ingest_service.heal_ledger_enabled()
    if not flags:
        return []

    screen_ids = {f.screen_id for f in flags}
    component_ids = {f.component_id for f in flags} | {
        f.candidate_component_id for f in flags if f.candidate_component_id
    }
    ledger_ids = {f.ledger_fact_id for f in flags if f.ledger_fact_id}

    screens_by_id = {
        s.id: s for s in db.query(PiScreen).filter(PiScreen.id.in_(screen_ids)).all()
    } if screen_ids else {}
    components_by_id = {
        c.id: c for c in db.query(PiComponent).filter(PiComponent.id.in_(component_ids)).all()
    } if component_ids else {}
    ledger_by_id = {
        row.id: row
        for row in db.query(SowRequirementsLedger)
        .filter(SowRequirementsLedger.id.in_(ledger_ids)).all()
    } if ledger_ids else {}

    out = []
    for f in flags:
        screen = screens_by_id.get(f.screen_id)
        component = components_by_id.get(f.component_id)
        candidate = components_by_id.get(f.candidate_component_id) if f.candidate_component_id else None
        ledger_row = ledger_by_id.get(f.ledger_fact_id) if f.ledger_fact_id else None

        out.append(PiDriftFlagOut(
            id=f.id, project_id=f.project_id, screen_id=f.screen_id, component_id=f.component_id,
            candidate_component_id=f.candidate_component_id, ledger_fact_id=f.ledger_fact_id,
            drift_type=f.drift_type.value, severity=f.severity, description=f.description,
            proposed_label=f.proposed_label, proposed_behavior_notes=f.proposed_behavior_notes,
            identity_tier=f.identity_tier, status=f.status.value,
            reviewed_by=f.reviewed_by, reviewed_at=f.reviewed_at,
            applied_ledger_fact_id=f.applied_ledger_fact_id,
            created_at=f.created_at, updated_at=f.updated_at,
            screen_route=screen.route if screen else None,
            component_label=component.label if component else None,
            component_locator=component.locator if component else None,
            candidate_component_label=candidate.label if candidate else None,
            ledger_current_label=ledger_row.label if ledger_row else None,
            ledger_current_behavior_notes=ledger_row.behavior_notes if ledger_row else None,
            heal_enabled=heal_enabled,
        ))
    return out


@router.get("/drift-flags", response_model=list[PiDriftFlagOut])
def list_drift_flags(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
    screen_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    drift_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    _feature_enabled()
    query = _project_scope(db.query(PiDriftFlag), PiDriftFlag, project_id)
    if screen_id is not None:
        query = query.filter(PiDriftFlag.screen_id == screen_id)
    if status_filter:
        query = query.filter(PiDriftFlag.status == status_filter)
    if drift_type:
        query = query.filter(PiDriftFlag.drift_type == drift_type)
    rows = query.order_by(PiDriftFlag.created_at.asc()).limit(limit).all()
    return _denormalize_drift_flags(db, rows)


@router.get("/drift-flags/{flag_id}", response_model=PiDriftFlagOut)
def get_drift_flag(
    flag_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
):
    _feature_enabled()
    row = db.get(PiDriftFlag, flag_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Drift flag not found")
    return _denormalize_drift_flags(db, [row])[0]


@router.post("/drift-flags/{flag_id}/apply", response_model=PiDriftFlagOut)
def apply_drift_flag(
    flag_id: uuid.UUID,
    payload: PiDriftFlagApplyIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Apply a pending drift flag's proposed healing (spec §19.3). 403s
    while PI_HEAL_LEDGER is off — detection and healing are independently
    switchable (spec §19.4); browsing/listing flags above never requires
    this flag, only writing through it does."""
    _feature_enabled()
    if not pi_ingest_service.heal_ledger_enabled():
        raise HTTPException(
            status_code=403,
            detail="Ledger healing is disabled (PI_HEAL_LEDGER is off). "
                   "The flag can still be reviewed, but not applied.",
        )

    flag, new_ledger_row = pi_heal_service.apply_heal(
        db, flag_id=flag_id, actor_user_id=current_user.id,
        label=payload.label, behavior_notes=payload.behavior_notes,
        confirm_pairing=payload.confirm_pairing,
    )
    if flag is None:
        raise HTTPException(
            status_code=404,
            detail="Drift flag not found, not pending, already applied, or pairing not confirmed",
        )
    db.commit()
    db.refresh(flag)

    write_audit_log(
        db, user_id=current_user.id, action="pi_drift_apply",
        resource_type="pi_drift_flag", resource_id=str(flag.id),
        details={
            "drift_type": flag.drift_type.value,
            "new_ledger_fact_id": str(new_ledger_row.id) if new_ledger_row else None,
        },
        ip_address=_client_ip(request),
    )
    logger.info(
        "Project Intelligence: %s applied drift flag %s (%s) for project %s%s",
        current_user.id, flag.id, flag.drift_type.value, flag.project_id,
        "" if new_ledger_row else " (no ledger match — reviewed only)",
    )
    return _denormalize_drift_flags(db, [flag])[0]


@router.post("/drift-flags/{flag_id}/reject", response_model=PiDriftFlagOut)
def reject_drift_flag(
    flag_id: uuid.UUID,
    payload: PiDriftFlagRejectIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Dismiss a pending flag outright — never applied. Unlike apply, this
    never touches the ledger, so it is NOT gated by PI_HEAL_LEDGER: a
    reviewer must always be able to clear a flag they've decided is noise,
    even while healing is off."""
    _feature_enabled()
    flag = pi_heal_service.reject_drift_flag(
        db, flag_id=flag_id, actor_user_id=current_user.id, reason=payload.reason,
    )
    if flag is None:
        raise HTTPException(status_code=404, detail="Drift flag not found or not pending")
    db.commit()
    db.refresh(flag)

    write_audit_log(
        db, user_id=current_user.id, action="pi_drift_reject",
        resource_type="pi_drift_flag", resource_id=str(flag.id),
        details={"reason": payload.reason}, ip_address=_client_ip(request),
    )
    return _denormalize_drift_flags(db, [flag])[0]


@router.post("/drift-flags/{flag_id}/reverse", response_model=PiDriftFlagOut)
def reverse_drift_flag(
    flag_id: uuid.UUID,
    payload: PiDriftFlagRejectIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence_review")),
):
    """Undo a previously-applied heal (spec §19.4) — clears superseded on
    the old ledger row, supersedes the new one, flips the flag to
    rejected. Gated by PI_HEAL_LEDGER exactly like apply, since it writes
    to sow_requirements_ledger too."""
    _feature_enabled()
    if not pi_ingest_service.heal_ledger_enabled():
        raise HTTPException(status_code=403, detail="Ledger healing is disabled (PI_HEAL_LEDGER is off)")

    flag = pi_heal_service.reverse_heal(
        db, flag_id=flag_id, actor_user_id=current_user.id, reason=payload.reason,
    )
    if flag is None:
        raise HTTPException(status_code=404, detail="Drift flag not found or not currently applied")
    db.commit()
    db.refresh(flag)

    write_audit_log(
        db, user_id=current_user.id, action="pi_drift_reverse",
        resource_type="pi_drift_flag", resource_id=str(flag.id),
        details={"reason": payload.reason}, ip_address=_client_ip(request),
    )
    return _denormalize_drift_flags(db, [flag])[0]


# ── AI Context Feedback Loop (Phase 4, spec §20) ────────────────────────────
#
# Read-only. The brief itself is built and injected in
# workers/tasks/ai_execution.py (never here — this endpoint exists purely
# so the before/after measurement is visible in the Admin UI). Not gated on
# PI_CONTEXT_ENABLED: the report is safe to view even while context
# injection is off (it will simply show zero "with_context" runs), same
# posture as the drift-flags list being viewable while PI_HEAL_LEDGER is
# off — visibility and the write/behavior-changing switch are independent.

@router.get("/context-effectiveness", response_model=PiContextEffectivenessOut)
def get_context_effectiveness(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("project_intelligence")),
    project_id: uuid.UUID | None = Query(default=None),
):
    _feature_enabled()
    from app.services import pi_context

    return pi_context.context_effectiveness_report(db, project_id=project_id)
