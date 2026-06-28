"""Audit log viewer API — Admin-only endpoint to query audit trail."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_roles
from app.core.logging import get_logger
from app.models.user import User, UserRole
from app.schemas.audit import AuditLogEntry, AuditLogListResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    user_id: Optional[UUID] = Query(None, description="Filter by user ID"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Return paginated audit log entries with optional filters. Admin only."""
    try:
        where_clauses = ["1=1"]
        params: list = []

        if user_id:
            params.append(str(user_id))
            where_clauses.append(f"al.user_id = ${len(params)}::uuid")

        if action:
            params.append(action)
            where_clauses.append(f"al.action = ${len(params)}")

        if resource_type:
            params.append(resource_type)
            where_clauses.append(f"al.resource_type = ${len(params)}")

        if from_date:
            params.append(from_date)
            where_clauses.append(f"al.created_at >= ${len(params)}::date")

        if to_date:
            params.append(to_date)
            where_clauses.append(f"al.created_at <= (${len(params)}::date + INTERVAL '1 day')")

        where_sql = " AND ".join(where_clauses)

        # Count total matching rows
        count_row = db.execute(
            text(f"SELECT COUNT(*) FROM audit_logs al WHERE {where_sql}"),
            params,
        ).fetchone()
        total = int(count_row[0] or 0)

        # Fetch paginated entries with joined user info
        params.extend([limit, offset])
        rows = db.execute(
            text(
                f"""
                SELECT
                    al.id,
                    u.email AS user_email,
                    u.full_name AS user_name,
                    al.action,
                    al.resource_type,
                    al.resource_id,
                    al.ip_address,
                    al.created_at
                FROM audit_logs al
                LEFT JOIN users u ON al.user_id = u.id
                WHERE {where_sql}
                ORDER BY al.created_at DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """
            ),
            params,
        ).fetchall()

        data = [
            AuditLogEntry(
                id=row[0],
                user_email=row[1],
                user_name=row[2],
                action=row[3],
                resource_type=row[4],
                resource_id=row[5],
                ip_address=row[6],
                created_at=row[7],
            )
            for row in rows
        ]

        logger.info(
            "Audit logs listed by %s (count=%d, total=%d)",
            current_user.id,
            len(data),
            total,
        )
        return AuditLogListResponse(data=data, total=total, page=(offset // limit) + 1, limit=limit)

    except Exception as exc:
        logger.error("Failed to list audit logs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list audit logs",
        )
