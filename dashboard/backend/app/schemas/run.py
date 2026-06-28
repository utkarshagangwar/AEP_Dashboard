"""Pydantic schemas for test run / execution endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.test_run import RunStatus


class RunCreate(BaseModel):
    """Request body for triggering a test run."""

    suite_id: UUID


class TestResultOut(BaseModel):
    """Single test result within a run."""

    id: UUID
    test_name: str
    status: str
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunResponse(BaseModel):
    """Response schema for a single test run."""

    id: UUID
    test_suite_id: UUID
    status: str
    triggered_by: Optional[UUID] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunSummary(BaseModel):
    """Aggregated stats for a run."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: int = 0


class RunDetailResponse(RunResponse):
    """Run detail with nested results and summary."""

    suite_name: Optional[str] = None
    project_name: Optional[str] = None
    triggered_by_name: Optional[str] = None
    summary: RunSummary = RunSummary()
    results: list[TestResultOut] = []


class RunListItem(BaseModel):
    """Run list item with joined names."""

    id: UUID
    test_suite_id: UUID
    suite_name: Optional[str] = None
    project_name: Optional[str] = None
    triggered_by_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    total: int = 0
    passed: int = 0
    failed: int = 0

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    """Paginated list of test runs."""

    data: list[RunListItem]
    total: int
    page: int
    limit: int
