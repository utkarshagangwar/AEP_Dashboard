"""Pydantic schemas for AI test run endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CredentialProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    project_id: Optional[UUID] = None
    allowed_domains: Optional[list[str]] = None
    credentials: Optional[dict] = None


class CredentialProfileResponse(BaseModel):
    id: UUID
    name: str
    project_id: Optional[UUID] = None
    allowed_domains: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIRunCreate(BaseModel):
    goal: str = Field(..., min_length=5, max_length=2000)
    project_id: Optional[UUID] = None
    credential_profile_id: Optional[UUID] = None
    environment: Optional[str] = Field(None, max_length=200)


class AIRunEventResponse(BaseModel):
    id: UUID
    run_id: UUID
    sequence: int
    status: str
    description: str
    step_type: str
    elapsed_ms: Optional[int] = None
    screenshot_url: Optional[str] = None
    highlighted_element: Optional[dict] = None
    is_failing_step: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class OrchestratorDecisionResponse(BaseModel):
    step: str
    invoked: bool
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    is_deterministic: bool = True
    rationale: str
    sequence: int = 0

    model_config = {"from_attributes": True}


class VisualFindingResponse(BaseModel):
    engine: str
    severity: str
    element: Optional[str] = None
    issue: str
    expected: Optional[str] = None
    actual: Optional[str] = None

    model_config = {"from_attributes": True}


class AIRunResponse(BaseModel):
    id: UUID
    goal: str
    environment: Optional[str] = None
    project_id: Optional[UUID] = None
    credential_profile_id: Optional[UUID] = None
    credential_profile_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    step_count: int = 0
    summary: Optional[str] = None
    raw_summary: Optional[str] = None
    run_type: str = "ai"
    skill_id: Optional[UUID] = None
    failing_step_index: Optional[int] = None
    failing_step_description: Optional[str] = None
    failing_step_screenshot_url: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    events: list[AIRunEventResponse] = []
    # Autonomous QA (orchestrator) runs only — empty/None for plain "ai" runs.
    error_message: Optional[str] = None
    ai_test_run_id: Optional[UUID] = None
    visual_run_id: Optional[UUID] = None
    self_execute_answer: Optional[str] = None
    pixel_mismatch_pct: Optional[int] = None
    decisions: list[OrchestratorDecisionResponse] = []
    findings: list[VisualFindingResponse] = []

    model_config = {"from_attributes": True}


class AIRunListItem(BaseModel):
    id: UUID
    goal: str
    environment: Optional[str] = None
    credential_profile_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    step_count: int = 0
    run_type: str = "ai"
    created_at: datetime

    model_config = {"from_attributes": True}


class AIRunListResponse(BaseModel):
    data: list[AIRunListItem]
    total: int
    page: int
    limit: int


class AISkillResponse(BaseModel):
    id: UUID
    name: str
    goal: str
    source_run_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    environment: Optional[str] = None
    credential_profile_id: Optional[UUID] = None
    step_count: int = 0
    times_replayed: int = 0
    last_replay_status: Optional[str] = None
    last_replayed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AISkillListResponse(BaseModel):
    data: list[AISkillResponse]
    total: int
    page: int
    limit: int


class SkillReplayRequest(BaseModel):
    credential_profile_id: Optional[UUID] = None
    allow_ai_fallback: bool = False
