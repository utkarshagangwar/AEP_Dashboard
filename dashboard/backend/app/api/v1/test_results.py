"""Test results API — list individual test case outcomes."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

router = APIRouter(prefix="/test-results", tags=["test-results"])


@router.get("")
def list_test_results(
    test_run_id: Optional[UUID] = Query(None),
    result_status: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paginated test results with optional filters."""
    try:
        offset = (page - 1) * limit
        where = "WHERE 1=1"
        params: dict = {}

        if test_run_id:
            params["test_run_id"] = str(test_run_id)
            where += " AND tr.test_run_id = :test_run_id"
        if result_status:
            params["result_status"] = result_status
            where += " AND tr.status = :result_status"

        count_row = db.execute(
            text(f"SELECT COUNT(*) FROM test_results tr {where}"),
            params,
        ).fetchone()
        total = int(count_row[0] or 0)

        params["limit_val"] = limit
        params["offset_val"] = offset
        rows = db.execute(
            text(
                f"""
                SELECT tr.id, tr.test_run_id, tr.test_name, tr.status,
                       tr.duration_ms, tr.error_message, tr.created_at,
                       trun.status AS run_status,
                       ts.name AS suite_name,
                       p.name AS project_name
                FROM test_results tr
                LEFT JOIN test_runs trun ON tr.test_run_id = trun.id
                LEFT JOIN test_suites ts ON trun.test_suite_id = ts.id
                LEFT JOIN projects p ON ts.project_id = p.id
                {where}
                ORDER BY tr.created_at DESC
                LIMIT :limit_val OFFSET :offset_val
                """
            ),
            params,
        ).fetchall()

        data = [
            {
                "id": str(r[0]),
                "test_run_id": str(r[1]) if r[1] else None,
                "test_name": r[2],
                "status": r[3].value if hasattr(r[3], "value") else r[3],
                "duration_ms": int(r[4]) if r[4] else None,
                "error_message": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
                "run_status": r[7].value if hasattr(r[7], "value") else r[7],
                "suite_name": r[8],
                "project_name": r[9],
            }
            for r in rows
        ]

        logger.info("Test results listed by %s (count=%d)", current_user.id, len(data))
        return {"data": data, "total": total, "page": page, "limit": limit}
    except Exception as exc:
        logger.error("List test results error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list test results",
        )
