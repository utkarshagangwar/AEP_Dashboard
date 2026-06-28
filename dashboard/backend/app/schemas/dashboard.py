"""Pydantic schemas for the dashboard statistics endpoint."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PassRateDay(BaseModel):
    """Pass rate for a single day."""
    date: str
    pass_rate: float
    total: int
    passed: int


class RunsByProject(BaseModel):
    """Run count per project."""
    project_name: str
    run_count: int


class DashboardKPICard(BaseModel):
    """Single KPI card data."""
    value: float | int
    change_pct: Optional[float] = None  # % change vs previous period
    label: str


class RecentRunItem(BaseModel):
    """A recent test run for the dashboard table."""
    id: UUID
    project_name: Optional[str] = None
    suite_name: Optional[str] = None
    status: str
    passed: int = 0
    failed: int = 0
    total: int = 0
    duration_ms: Optional[int] = None
    created_at: str


class TopDefectItem(BaseModel):
    """An open/in-progress defect for the dashboard widget."""
    id: UUID
    title: str
    severity: str
    assigned_to_name: Optional[str] = None
    created_at: str


class DashboardStatsResponse(BaseModel):
    """Complete dashboard stats response — single endpoint."""
    total_runs_today: DashboardKPICard
    pass_rate_7d: DashboardKPICard
    open_defects: DashboardKPICard
    critical_defects: DashboardKPICard
    active_projects: DashboardKPICard
    avg_execution_duration: DashboardKPICard
    pass_rate_by_day: list[PassRateDay]
    runs_by_project: list[RunsByProject]
    recent_runs: list[RecentRunItem]
    top_defects: list[TopDefectItem]
