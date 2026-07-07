"""Pydantic schemas for project endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Request body for creating a project."""

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    """Request body for updating a project (PATCH semantics)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectResponse(BaseModel):
    """Response schema for a single project."""

    id: UUID
    name: str
    description: Optional[str] = None
    is_active: bool
    suite_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Envelope for the project list endpoint, matching the {data, total,
    page, limit} shape used by every other list endpoint in the API."""

    data: list[ProjectResponse]
    total: int
    page: int
    limit: int


class ProjectDetailResponse(ProjectResponse):
    """Project detail response including nested test suites."""

    suites: list["SuiteSummary"] = []


class SuiteSummary(BaseModel):
    """Minimal suite info nested in project detail."""

    id: UUID
    name: str
    suite_type: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
