"""Pydantic schemas for the Project Intelligence API.

Phase 1 (Foundation & Flow) — screens, navigation edges, components,
behaviour notes, the flow model, review actions, and the unified review
queue. Phase 2 (Change Detection & Healing) adds the drift-flag schemas
below — kept deliberately separate from PiReviewActionIn/PiEntityType
above, since ledger healing is a dedicated, non-bulk operation (spec
§19.4), not a fifth entity type in the generic review-action endpoint.

Phase 3 (Active Crawler & Visual) adds the design-pattern schemas at the
bottom. Unlike drift flags, a design pattern carries no high-risk write
(nothing outside pi_design_patterns is ever touched), so it IS folded into
the generic entity-type/review-action machinery — "design_pattern" is a
fifth PiEntityType, alongside screen/component/behavior_note/flow.

Phase 4 (AI Context Feedback Loop) adds PiContextEffectivenessOut at the
very bottom — the read-only before/after measurement shape spec §20 asks
for. It has no corresponding DB model: services/pi_context.py computes it
on the fly from the existing ai_usage_events table.
"""
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Screens ───────────────────────────────────────────────────────────────

class PiScreenOut(BaseModel):
    id: UUID
    project_id: UUID
    environment_id: Optional[UUID]
    route: str
    title: Optional[str]
    description: Optional[str]
    source_type: str
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PiScreenUpdate(BaseModel):
    # Reviewer edit-then-verify (spec §22): only content fields are
    # editable here. status transitions go through the review-action
    # endpoints below, never a bare PATCH, so every state change is
    # attributed and audited.
    title: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = None


# ── Navigation edges ─────────────────────────────────────────────────────

class PiNavigationEdgeOut(BaseModel):
    id: UUID
    project_id: UUID
    from_screen_id: UUID
    to_screen_id: UUID
    trigger_action: Optional[str]
    observed_count: int
    last_observed_at: Optional[datetime]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Components ───────────────────────────────────────────────────────────

class PiComponentOut(BaseModel):
    id: UUID
    project_id: UUID
    screen_id: UUID
    component_key: str
    identity_tier: int
    component_type: str
    label: str
    previous_label: Optional[str]
    locator: Optional[str]
    locator_strategy: Optional[str]
    success_count: int
    fail_count: int
    status: str
    last_seen_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PiComponentUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=500)
    locator: Optional[str] = None
    locator_strategy: Optional[str] = Field(default=None, max_length=50)


# ── Behaviour notes ──────────────────────────────────────────────────────

class PiBehaviorNoteOut(BaseModel):
    id: UUID
    project_id: UUID
    screen_id: UUID
    description: str
    source_type: str
    source_ref: Optional[str]
    confidence: Optional[float]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class PiBehaviorNoteUpdate(BaseModel):
    description: str = Field(..., min_length=1)


# ── Flow model ───────────────────────────────────────────────────────────

class PiFlowStateIn(BaseModel):
    """One state, in exactly the shape flow_validation.build_index() reads."""

    id: str = Field(..., min_length=1, max_length=100)
    name: Optional[str] = None
    requires: list[str] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    # Human-authored only — see the module docstring on why this is never
    # machine-proposed. Enforced in services/pi_flow.py, not by this schema
    # alone, since the same shape is also written by propose_model().
    locked_behaviours: list[str] = Field(default_factory=list)


class PiFlowModelIn(BaseModel):
    entry_state: Optional[str] = None
    states: list[PiFlowStateIn] = Field(default_factory=list)


class PiFlowOut(BaseModel):
    id: UUID
    project_id: UUID
    environment_id: Optional[UUID]
    version: int
    model_json: dict[str, Any]
    status: str
    generated_from_run_ids: Optional[list[str]]
    edited_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PiFlowCreate(BaseModel):
    """A human authoring or editing a flow model from scratch — always
    creates a new pending version (spec §17.3); never mutates a verified
    row in place."""

    project_id: UUID
    environment_id: Optional[UUID] = None
    model: PiFlowModelIn


# ── Review actions / queue ───────────────────────────────────────────────

PiEntityType = Literal["screen", "component", "behavior_note", "flow", "design_pattern"]
PiReviewAction = Literal["approve", "edit", "reject"]


class PiReviewActionIn(BaseModel):
    action: PiReviewAction
    # Required by the API for 'reject' (validated in the endpoint, not
    # here, since the requirement is conditional on `action`).
    reason: Optional[str] = Field(default=None, max_length=2000)
    # Only meaningful with action="edit" — the corrected fields to apply
    # before flipping the row to verified. Shape depends on entity_type;
    # validated against PiScreenUpdate/PiComponentUpdate/
    # PiBehaviorNoteUpdate server-side.
    edit: Optional[dict[str, Any]] = None


class PiReviewActionOut(BaseModel):
    id: UUID
    project_id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    reason: Optional[str]
    actor_user_id: Optional[UUID]
    created_at: datetime

    class Config:
        from_attributes = True


class PiQueueItemOut(BaseModel):
    """One row in the unified Review Queue (spec §21.3) — assembled
    server-side across pi_screens / pi_components / pi_behavior_notes /
    pi_flows, oldest first. Not a DB row; a projection."""

    entity_type: PiEntityType
    entity_id: UUID
    project_id: UUID
    summary: str
    status: str
    # False for pi_flows -- a flow model is reviewed as a whole version,
    # not bulk-selected alongside unrelated screens/components (spec §22 /
    # the prototype's "not bulk-eligible" row).
    bulk_eligible: bool
    submitted_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


# ── Drift flags (Phase 2 — Change Detection & Healing, spec §18-19) ─────────
# Deliberately NOT part of PiEntityType/PiReviewActionIn above and NOT
# surfaced through the unified review queue: healing is a dedicated,
# one-at-a-time, diff-visible operation (spec §19.4 — "Bulk approve is
# available for screens and components; it is not available for ledger
# healing"), so it gets its own endpoints (api/v1/project_intelligence.py)
# and its own schemas here rather than reusing the generic shape.

# Named distinctly from app.models.project_intelligence.PiDriftType (the
# ORM PyEnum) so a file that needs both (api/v1/project_intelligence.py)
# never has an accidental same-name import collision between the two.
PiDriftTypeLiteral = Literal[
    "label_changed", "candidate_rename", "locator_broken",
    "removed", "added", "behavior_changed",
]


class PiDriftFlagOut(BaseModel):
    id: UUID
    project_id: UUID
    screen_id: UUID
    component_id: UUID
    candidate_component_id: Optional[UUID]
    ledger_fact_id: Optional[UUID]
    drift_type: PiDriftTypeLiteral
    severity: str
    description: str
    proposed_label: Optional[str]
    proposed_behavior_notes: Optional[str]
    identity_tier: Optional[int]
    status: str
    reviewed_by: Optional[UUID]
    reviewed_at: Optional[datetime]
    applied_ledger_fact_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    # Denormalized for the Change & Drift Log diff view, so the frontend
    # never has to issue N extra lookups per row. Populated server-side in
    # the list/get endpoints, not stored on the row itself.
    screen_route: Optional[str] = None
    component_label: Optional[str] = None
    component_locator: Optional[str] = None
    candidate_component_label: Optional[str] = None
    ledger_current_label: Optional[str] = None
    ledger_current_behavior_notes: Optional[str] = None
    # Whether PI_HEAL_LEDGER is currently on — lets the UI grey out Apply
    # with an explanation instead of letting the click 403 (spec §19.4's
    # kill switch: "Off -> the flag is still raised and shown, but the
    # apply button 403s").
    heal_enabled: bool = False

    class Config:
        from_attributes = True


class PiDriftFlagApplyIn(BaseModel):
    """Apply = confirm the proposed healing (spec §19.3). label/
    behavior_notes may be overridden here — the reviewer correcting the
    machine's suggestion before it is written, same edit-then-verify
    pattern as every other Project Intelligence entity — and default to
    the flag's own proposed_label/proposed_behavior_notes when omitted.
    Only meaningful for candidate_rename: `confirm_pairing=False` rejects
    the proposed pairing without rejecting the underlying drift outright
    (spec §18.4: "the pairing is surfaced ... and a human decides")."""

    label: Optional[str] = Field(default=None, max_length=500)
    behavior_notes: Optional[str] = None
    confirm_pairing: bool = True


class PiDriftFlagRejectIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


# ── Design patterns (Phase 3 — Active Crawler & Visual, spec §14.3/table 8) ──

PiPatternTypeLiteral = Literal["color", "typography", "layout", "component_style"]


class PiDesignPatternOut(BaseModel):
    id: UUID
    project_id: UUID
    screen_id: Optional[UUID]
    pattern_type: str
    value: dict[str, Any]
    description: Optional[str]
    evidence_ref: Optional[str]
    confidence: Optional[float]
    status: str
    created_at: datetime
    updated_at: datetime

    # Denormalized, same convention as PiDriftFlagOut — the Design Library
    # view groups by screen without a second round-trip per row.
    screen_route: Optional[str] = None

    class Config:
        from_attributes = True


class PiDesignPatternUpdate(BaseModel):
    # Reviewer edit-then-verify (spec §22), same shape as PiScreenUpdate/
    # PiComponentUpdate: content-only, status transitions go through
    # review-action only.
    pattern_type: Optional[str] = Field(default=None, max_length=50)
    value: Optional[dict[str, Any]] = None
    description: Optional[str] = None


# ── AI Context Feedback Loop (Phase 4, spec §20) ────────────────────────────

class PiContextGroupStats(BaseModel):
    run_count: int
    avg_steps: float
    avg_tokens: float
    avg_cost_usd: float


class PiContextEffectivenessOut(BaseModel):
    """services/pi_context.py:context_effectiveness_report()'s return shape.
    No DB model — computed on the fly from ai_usage_events, which already
    records everything this needs (spec §20: "Phase 4 delivers a
    before/after comparison, not a claim")."""

    with_context: PiContextGroupStats
    without_context: PiContextGroupStats
    note: str
