"""Pydantic schemas for defect endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.defect import DefectSeverity, DefectStatus


class DefectCreate(BaseModel):
    """Request body for creating a defect."""

    result_id: Optional[UUID] = Field(None, alias="test_result_id")
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = None
    severity: DefectSeverity = DefectSeverity.medium
    project_id: Optional[UUID] = None


class DefectUpdate(BaseModel):
    """Request body for updating a defect."""

    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = None
    severity: Optional[DefectSeverity] = None
    status: Optional[DefectStatus] = None
    assigned_to: Optional[UUID] = None


class DefectResponse(BaseModel):
    """Response schema for a single defect."""

    id: UUID
    test_result_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    severity: DefectSeverity
    status: DefectStatus
    reported_by: Optional[UUID] = None
    assigned_to: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DefectDetailResponse(DefectResponse):
    """Defect detail with joined names."""

    project_name: Optional[str] = None
    reported_by_name: Optional[str] = None
    assigned_to_name: Optional[str] = None
    linked_test_name: Optional[str] = None


class DefectListItem(BaseModel):
    """Defect list item with joined names."""

    id: UUID
    title: str
    description: Optional[str] = None
    severity: DefectSeverity
    status: DefectStatus
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    reported_by_name: Optional[str] = None
    assigned_to: Optional[UUID] = None
    assigned_to_name: Optional[str] = None
    linked_test_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DefectListResponse(BaseModel):
    """Paginated list of defects."""

    data: list[DefectListItem]
    total: int
    page: int
    limit: int
