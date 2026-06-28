"""Pydantic schemas for test suite endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.test_suite import SuiteType


class SuiteCreate(BaseModel):
    """Request body for creating a test suite."""

    name: str = Field(min_length=1, max_length=255)
    suite_type: SuiteType
    description: Optional[str] = None


class SuiteUpdate(BaseModel):
    """Request body for updating a test suite (PATCH semantics)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    suite_type: Optional[SuiteType] = None
    description: Optional[str] = None


class SuiteResponse(BaseModel):
    """Response schema for a single test suite."""

    id: UUID
    project_id: UUID
    name: str
    suite_type: Optional[SuiteType] = None
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
