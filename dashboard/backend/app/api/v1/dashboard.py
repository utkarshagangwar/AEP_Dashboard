"""Dashboard API — single endpoint returning all dashboard statistics."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.dashboard import DashboardStatsResponse
from app.services.dashboard_service import get_dashboard_stats

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
def dashboard_stats(
    project_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all dashboard statistics in a single response.

    Pass ``project_id`` to scope every metric to a single project.
    """
    try:
        from app.api.v1.reports import _reconcile_stale_runs
        _reconcile_stale_runs(db)

        logger.info("Dashboard stats requested by %s", current_user.id)
        data = get_dashboard_stats(
            db,
            project_id=str(project_id) if project_id else None,
        )
        return DashboardStatsResponse(**data)
    except SQLAlchemyError as exc:
        logger.error("Dashboard stats DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load dashboard statistics",
        ) from exc
    except Exception as exc:
        logger.error("Dashboard stats error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load dashboard statistics",
        ) from exc
