"""Top-level test suite listing — flat endpoint for frontend convenience."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

router = APIRouter(prefix="/test-suites", tags=["test-suites"])


@router.get("")
def list_all_suites(
    project_id: Optional[UUID] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return active suites, optionally filtered by project."""
    try:
        where = "WHERE 1=1"
        params: dict = {"limit_val": limit}

        if project_id:
            params["project_id"] = str(project_id)
            where += " AND ts.project_id = :project_id"

        rows = db.execute(
            text(
                f"""
                SELECT ts.id, ts.project_id, ts.name, ts.description,
                       ts.created_at, ts.updated_at,
                       p.name AS project_name
                FROM test_suites ts
                LEFT JOIN projects p ON ts.project_id = p.id
                {where}
                ORDER BY ts.created_at DESC
                LIMIT :limit_val
                """
            ),
            params,
        ).fetchall()

        data = [
            {
                "id": str(r[0]),
                "project_id": str(r[1]),
                "name": r[2],
                "description": r[3],
                "is_active": True,
                "suite_type": None,
                "created_at": r[4].isoformat() if r[4] else None,
                "updated_at": r[5].isoformat() if r[5] else None,
                "project_name": r[6],
            }
            for r in rows
        ]

        logger.info("Suites listed by %s (count=%d)", current_user.id, len(data))
        return data
    except Exception as exc:
        logger.error("List suites error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
