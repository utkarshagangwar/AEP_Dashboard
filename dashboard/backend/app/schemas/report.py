"""Pydantic schemas for report endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ReportRunItem(BaseModel):
    """Single run in a report list."""

    id: UUID
    suite_name: Optional[str] = None
    suite_type: Optional[str] = None
    project_name: Optional[str] = None
    project_id: Optional[UUID] = None
    status: str
    triggered_by_name: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: int = 0

    model_config = {"from_attributes": True}


class ReportListResponse(BaseModel):
    """Paginated list of report runs."""

    data: list[ReportRunItem]
    total: int
    page: int
    limit: int


class ReportResultOut(BaseModel):
    """Single test result in a report."""

    id: UUID
    test_name: str
    status: str
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    source_suite: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime
    defect_count: int = 0

    model_config = {"from_attributes": True}


class ReportDetailResponse(BaseModel):
    """Full run report with results."""

    id: UUID
    status: str
    suite_name: Optional[str] = None
    suite_type: Optional[str] = None
    project_name: Optional[str] = None
    triggered_by_name: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: int = 0
    defect_count: int = 0
    results: list[ReportResultOut] = []


class ReportSummaryResponse(BaseModel):
    """Aggregate summary stats for the reports dashboard."""

    total_runs: int = 0
    pass_rate: float = 0.0
    avg_duration_ms: int = 0
    runs_per_project: list[dict] = []
