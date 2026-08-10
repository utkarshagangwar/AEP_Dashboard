"""Pydantic schemas for the AI Usage admin endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AIUsageEventEntry(BaseModel):
    """A single logged LLM call."""
    id: UUID
    created_at: datetime
    source: str
    provider: str
    model: str
    key_label: Optional[str] = None
    status: str
    http_status: Optional[int] = None
    error_message: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    attempts: Optional[int] = None
    run_type: Optional[str] = None
    run_id: Optional[str] = None

    model_config = {"from_attributes": True}


class AIUsageEventListResponse(BaseModel):
    data: list[AIUsageEventEntry]
    total: int
    page: int
    limit: int


class AIProviderBreakdown(BaseModel):
    provider: str
    calls: int
    tokens: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float


class AIUsageSummary(BaseModel):
    """Aggregate numbers for the summary cards. window_days describes the
    lookback the totals were computed over (None = all time)."""
    window_days: Optional[int]
    total_calls: int
    total_tokens: int
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float
    failed_calls: int
    by_provider: list[AIProviderBreakdown]


class AIKeyUsage(BaseModel):
    """One configured key's usage + remaining-quota status."""
    key_label: str
    provider: str
    calls_period: int  # calls in the relevant reset period (today for daily quotas, all-time for budgets)
    tokens_period: int
    prompt_tokens_period: int = 0
    completion_tokens_period: int = 0
    cost_period_usd: float
    limit_type: Optional[str] = None  # "requests_per_day" | "budget_usd" | None
    limit_value: Optional[float] = None
    limit_source: Optional[str] = None  # "auto" | "manual" | None
    remaining: Optional[float] = None
    quota_status: str  # "ok" | "exhausted" | "unknown"
    resets_at: Optional[str] = None  # human-readable, e.g. "midnight PT"
    last_used_at: Optional[datetime] = None


class AIKeyLimitUpsert(BaseModel):
    limit_type: str = Field(..., pattern="^(requests_per_day|budget_usd)$")
    limit_value: float = Field(..., gt=0)
    note: Optional[str] = None


class AITaskGroup(BaseModel):
    """One user-triggered task (a Vibe Test run, a SOW import, a Visual
    Audit, ...) and the aggregate of every AI call logged under it — see
    app.services.ai_usage.list_task_groups. is_legacy=True groups calls
    logged before task-tracking existed (or made outside any tracked task),
    bucketed per source rather than dropped."""
    run_type: Optional[str] = None
    run_id: Optional[str] = None
    is_legacy: bool = False
    legacy_source: Optional[str] = None
    label: str
    task_kind_label: Optional[str] = None
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    status: str  # "ok" | "error" | "partial"
    first_seen: datetime
    last_seen: datetime
    sources: list[str] = []
    providers: list[str] = []


class AITaskGroupListResponse(BaseModel):
    data: list[AITaskGroup]
    total: int
    page: int
    limit: int
