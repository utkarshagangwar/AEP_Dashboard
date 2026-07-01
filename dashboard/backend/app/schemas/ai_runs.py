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
    failing_step_index: Optional[int] = None
    failing_step_description: Optional[str] = None
    failing_step_screenshot_url: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    events: list[AIRunEventResponse] = []

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
    created_at: datetime

    model_config = {"from_attributes": True}


class AIRunListResponse(BaseModel):
    data: list[AIRunListItem]
    total: int
    page: int
    limit: int
