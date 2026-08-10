"""AI Usage viewer + per-key quota API — Admin-only.

Read side backs the AI Usage admin page (calls/tokens/cost table + summary
cards + per-key "usage left"). Write side is a single small endpoint that
lets an admin set/clear a manual quota override for a key — see
app/services/ai_usage.py's module docstring for why most providers need
that (no reliable auto-detected limit exists for them).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_roles
from app.core.logging import get_logger
from app.models.ai_usage import AIKeyLimit, AIUsageEvent
from app.models.user import User, UserRole
from app.schemas.ai_usage import (
    AIKeyLimitUpsert,
    AIKeyUsage,
    AIProviderBreakdown,
    AITaskGroup,
    AITaskGroupListResponse,
    AIUsageEventEntry,
    AIUsageEventListResponse,
    AIUsageSummary,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])


@router.get("", response_model=AIUsageEventListResponse)
def list_usage_events(
    provider: Optional[str] = Query(None, description="Filter by provider (google/axon/openrouter/anthropic/openai)"),
    source: Optional[str] = Query(None, description="Filter by subsystem (hands/judge/brain/sow_ledger/...)"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (ok/error)"),
    key_label: Optional[str] = Query(None, description="Filter by masked key label"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    run_type: Optional[str] = Query(None, description="Filter to one task's calls (with run_id) — see ai_usage.tracked_task"),
    run_id: Optional[str] = Query(None, description="Filter to one task's calls (with run_type)"),
    no_task: bool = Query(False, description="Filter to calls with no task id at all (the 'legacy' bucket); combine with source= to match one bucket"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Paginated, filtered event log. Excludes source="quota_probe" by
    default (those are cheap liveness checks, not real LLM calls) unless
    explicitly requested via source=quota_probe.

    run_type+run_id / no_task exist for the "Usage events by task" view
    (GET /ai-usage/tasks) to fetch one task's calls on demand when its row
    is expanded — see app.services.ai_usage.list_task_groups."""
    try:
        query = db.query(AIUsageEvent)
        if source:
            query = query.filter(AIUsageEvent.source == source)
        else:
            query = query.filter(AIUsageEvent.source != "quota_probe")
        if provider:
            query = query.filter(AIUsageEvent.provider == provider)
        if status_filter:
            query = query.filter(AIUsageEvent.status == status_filter)
        if key_label:
            query = query.filter(AIUsageEvent.key_label == key_label)
        if from_date:
            query = query.filter(AIUsageEvent.created_at >= datetime.fromisoformat(from_date))
        if to_date:
            query = query.filter(
                AIUsageEvent.created_at < (datetime.fromisoformat(to_date) + timedelta(days=1))
            )
        if no_task:
            query = query.filter(AIUsageEvent.run_id.is_(None))
        elif run_type and run_id:
            query = query.filter(AIUsageEvent.run_type == run_type, AIUsageEvent.run_id == run_id)

        total = query.count()
        rows = (
            query.order_by(AIUsageEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        data = [AIUsageEventEntry.model_validate(r) for r in rows]

        return AIUsageEventListResponse(
            data=data, total=total, page=(offset // limit) + 1, limit=limit
        )
    except Exception as exc:
        logger.error("Failed to list AI usage events: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list AI usage events",
        )


@router.get("/tasks", response_model=AITaskGroupListResponse)
def list_task_groups(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    source: Optional[str] = Query(None, description="Filter by subsystem"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by call status (ok/error)"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Usage events grouped by the task that made them (a Vibe Test run, a
    SOW import, a Visual Audit, ...) — see app.services.ai_usage's "Task
    grouping" section. Aggregates only; a group's individual calls are
    fetched via GET /ai-usage (run_type+run_id, or no_task=true&source=...
    for a legacy bucket) when its row is expanded."""
    try:
        from app.services import ai_usage

        from_dt = datetime.fromisoformat(from_date) if from_date else None
        to_dt = (datetime.fromisoformat(to_date) + timedelta(days=1)) if to_date else None

        groups, total = ai_usage.list_task_groups(
            db,
            provider=provider,
            source=source,
            status_filter=status_filter,
            from_date=from_dt,
            to_date=to_dt,
            limit=limit,
            offset=offset,
        )
        return AITaskGroupListResponse(
            data=[AITaskGroup(**g) for g in groups],
            total=total,
            page=(offset // limit) + 1,
            limit=limit,
        )
    except Exception as exc:
        logger.error("Failed to list AI usage task groups: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list AI usage task groups",
        )


@router.get("/summary", response_model=AIUsageSummary)
def usage_summary(
    window_days: Optional[int] = Query(30, ge=0, le=365, description="Lookback window in days; 0 for all-time (used by the 'By provider' card, which is always cumulative regardless of the summary cards' own window)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Totals + per-provider breakdown for the summary cards. Real LLM
    calls only — source="quota_probe" rows never count towards these."""
    try:
        from sqlalchemy import func

        query = db.query(AIUsageEvent).filter(AIUsageEvent.source != "quota_probe")
        if window_days:
            since = datetime.now(timezone.utc) - timedelta(days=window_days)
            query = query.filter(AIUsageEvent.created_at >= since)

        total_calls = query.count()
        failed_calls = query.filter(AIUsageEvent.status == "error").count()

        totals = query.with_entities(
            func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
        ).one()
        total_tokens, total_prompt_tokens, total_completion_tokens, total_cost = totals

        by_provider_rows = (
            query.with_entities(
                AIUsageEvent.provider,
                func.count(AIUsageEvent.id),
                func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.completion_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
            )
            .group_by(AIUsageEvent.provider)
            .order_by(func.count(AIUsageEvent.id).desc())
            .all()
        )
        by_provider = [
            AIProviderBreakdown(
                provider=row[0],
                calls=row[1],
                tokens=int(row[2] or 0),
                prompt_tokens=int(row[3] or 0),
                completion_tokens=int(row[4] or 0),
                cost_usd=float(row[5] or 0),
            )
            for row in by_provider_rows
        ]

        return AIUsageSummary(
            window_days=window_days,
            total_calls=total_calls,
            total_tokens=int(total_tokens or 0),
            total_prompt_tokens=int(total_prompt_tokens or 0),
            total_completion_tokens=int(total_completion_tokens or 0),
            total_cost_usd=float(total_cost or 0),
            failed_calls=failed_calls,
            by_provider=by_provider,
        )
    except Exception as exc:
        logger.error("Failed to compute AI usage summary: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute AI usage summary",
        )


@router.get("/keys", response_model=list[AIKeyUsage])
def key_usage(
    period_start: Optional[str] = Query(
        None,
        description="Override the display period's start (YYYY-MM-DD). "
        "Omit for the current calendar month. Never affects Remaining/"
        "quota_status — see compute_key_usage's docstring.",
    ),
    period_end: Optional[str] = Query(
        None, description="Display period end, exclusive (YYYY-MM-DD). Omit for open-ended (through now)."
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Per-key usage + remaining-quota status — every key currently
    configured in the environment, even ones with zero calls logged yet.
    See app.services.ai_usage.compute_key_usage for the calculation, and
    for why period_start/period_end only ever affect the displayed
    calls/tokens/cost, never Remaining or quota_status."""
    try:
        from app.services import ai_usage

        start_dt = datetime.fromisoformat(period_start) if period_start else None
        end_dt = (datetime.fromisoformat(period_end) + timedelta(days=1)) if period_end else None

        rows = ai_usage.compute_key_usage(db, period_start=start_dt, period_end=end_dt)
        return [AIKeyUsage(**row) for row in rows]
    except Exception as exc:
        logger.error("Failed to compute per-key AI usage: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute per-key AI usage",
        )


@router.put("/keys/{key_label}/limit", response_model=AIKeyUsage)
def set_key_limit(
    key_label: str,
    body: AIKeyLimitUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Set (or replace) a manual quota override for one key_label. The
    provider is inferred from key_label's "<provider>:..." prefix rather
    than taken from the request body, so it can never drift from the
    events actually logged under that label."""
    provider = key_label.split(":", 1)[0] if ":" in key_label else "unknown"

    existing = db.query(AIKeyLimit).filter(AIKeyLimit.key_label == key_label).one_or_none()
    if existing:
        existing.limit_type = body.limit_type
        existing.limit_value = body.limit_value
        existing.note = body.note
        existing.updated_by = current_user.id
    else:
        existing = AIKeyLimit(
            id=uuid.uuid4(),
            key_label=key_label,
            provider=provider,
            limit_type=body.limit_type,
            limit_value=body.limit_value,
            note=body.note,
            updated_by=current_user.id,
        )
        db.add(existing)
    db.commit()

    logger.info(
        "AI usage: %s set %s limit for %s to %s",
        current_user.email, body.limit_type, key_label, body.limit_value,
    )

    from app.services import ai_usage

    rows = ai_usage.compute_key_usage(db)
    for row in rows:
        if row["key_label"] == key_label:
            return AIKeyUsage(**row)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Key is no longer configured in the environment",
    )


@router.delete("/keys/{key_label}/limit", status_code=status.HTTP_200_OK)
def clear_key_limit(
    key_label: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Remove a manual override — the key falls back to its auto-detected
    default (AXON's budget) or "no fixed limit". Returns 200 + a small JSON
    body (not 204) — the frontend apiClient.apiDelete() always calls
    res.json(), same convention as projects.delete_project()."""
    deleted = db.query(AIKeyLimit).filter(AIKeyLimit.key_label == key_label).delete()
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No override set for this key")
    logger.info("AI usage: %s cleared manual limit for %s", current_user.email, key_label)
    return {"detail": "Limit cleared"}
