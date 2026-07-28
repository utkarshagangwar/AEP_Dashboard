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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Paginated, filtered event log. Excludes source="quota_probe" by
    default (those are cheap liveness checks, not real LLM calls) unless
    explicitly requested via source=quota_probe."""
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


@router.get("/summary", response_model=AIUsageSummary)
def usage_summary(
    window_days: Optional[int] = Query(30, ge=1, le=365, description="Lookback window in days; omit for all-time"),
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
            func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
        ).one()
        total_tokens, total_cost = totals

        by_provider_rows = (
            query.with_entities(
                AIUsageEvent.provider,
                func.count(AIUsageEvent.id),
                func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(AIUsageEvent.cost_usd), 0),
            )
            .group_by(AIUsageEvent.provider)
            .order_by(func.count(AIUsageEvent.id).desc())
            .all()
        )
        by_provider = [
            AIProviderBreakdown(
                provider=row[0], calls=row[1], tokens=int(row[2] or 0), cost_usd=float(row[3] or 0)
            )
            for row in by_provider_rows
        ]

        return AIUsageSummary(
            window_days=window_days,
            total_calls=total_calls,
            total_tokens=int(total_tokens or 0),
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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Per-key usage + remaining-quota status — every key currently
    configured in the environment, even ones with zero calls logged yet.
    See app.services.ai_usage.compute_key_usage for the calculation."""
    try:
        from app.services import ai_usage

        rows = ai_usage.compute_key_usage(db)
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
