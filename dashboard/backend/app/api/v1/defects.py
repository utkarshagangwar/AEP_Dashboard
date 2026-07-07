"""Defects API — CRUD with RBAC and status transitions."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.defect import DefectSeverity, DefectStatus
from app.models.user import User, UserRole
from app.schemas.defect import (
    DefectCreate,
    DefectDetailResponse,
    DefectListResponse,
    DefectListItem,
    DefectResponse,
    DefectUpdate,
)
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/defects", tags=["defects"])

# Allowed status transitions
VALID_TRANSITIONS = {
    DefectStatus.open: {DefectStatus.in_progress},
    DefectStatus.in_progress: {DefectStatus.resolved},
    DefectStatus.resolved: {DefectStatus.closed, DefectStatus.open},
    DefectStatus.closed: {DefectStatus.open},
}


def _validate_transition(current: DefectStatus, target: DefectStatus) -> None:
    """Raise 422 if the status transition is not allowed."""
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status transition: {current.value} → {target.value}. "
            f"Allowed transitions from {current.value}: "
            f"{[s.value for s in allowed]}",
        )


@router.post("", response_model=DefectResponse, status_code=status.HTTP_201_CREATED)
def create_defect(
    body: DefectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission("defects")
    ),
):
    """Create a defect linked to a failed test result."""
    try:
        title = body.title.strip()

        # Validate test_result_id if provided — must point to a failed result
        if body.result_id:
            result_row = db.execute(
                text(
                    "SELECT id, status FROM test_results WHERE id = :rid"
                ),
                {"rid": str(body.result_id)},
            ).fetchone()
            if not result_row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Test result not found",
                )
            if result_row[1] != "failed":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Can only log defects for failed test results",
                )

        row = db.execute(
            text(
                """
                INSERT INTO defects (test_result_id, project_id, title, description, severity, status, reported_by)
                VALUES (:result_id, :project_id, :title, :description, :severity, 'open', :reported_by)
                RETURNING id, test_result_id, project_id, title, description, severity,
                          status, reported_by, assigned_to, created_at, updated_at
                """
            ),
            {
                "result_id": str(body.result_id) if body.result_id else None,
                "project_id": str(body.project_id) if body.project_id else None,
                "title": title,
                "description": body.description,
                "severity": body.severity.value,
                "reported_by": str(current_user.id),
            },
        ).fetchone()
        db.commit()

        defect_id = row[0]
        write_audit_log(
            db,
            user_id=current_user.id,
            action="defect_created",
            resource_type="defect",
            resource_id=str(defect_id),
            details={"title": title, "severity": body.severity.value},
        )

        logger.info("Defect created: %s by %s", defect_id, current_user.id)
        return DefectResponse(
            id=row[0],
            test_result_id=row[1],
            project_id=row[2],
            title=row[3],
            description=row[4],
            severity=row[5],
            status=row[6],
            reported_by=row[7],
            assigned_to=row[8],
            created_at=row[9],
            updated_at=row[10],
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Failed to create defect: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create defect",
        )


@router.get("", response_model=DefectListResponse)
def list_defects(
    project_id: Optional[UUID] = Query(None),
    severity: Optional[str] = Query(None),
    defect_status: Optional[str] = Query(None, alias="status"),
    assigned_to: Optional[UUID] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List defects with filters. Developers only see their assigned defects."""
    try:
        offset = (page - 1) * limit
        where = "WHERE 1=1"
        params: dict = {}

        # Developer restriction
        if current_user.role == UserRole.developer:
            params["user_id"] = str(current_user.id)
            where += " AND d.assigned_to = :user_id"

        if project_id:
            params["project_id"] = str(project_id)
            where += " AND d.project_id = :project_id"
        if severity:
            params["severity"] = severity
            where += " AND d.severity = :severity"
        if defect_status:
            params["defect_status"] = defect_status
            where += " AND d.status = :defect_status"
        if assigned_to:
            params["assigned_to"] = str(assigned_to)
            where += " AND d.assigned_to = :assigned_to"

        # Count
        count_row = db.execute(
            text(f"SELECT COUNT(*) FROM defects d {where}"),
            params,
        ).fetchone()
        total = int(count_row[0] or 0)

        # Fetch
        params["limit_val"] = limit
        params["offset_val"] = offset
        rows = db.execute(
            text(
                f"""
                SELECT d.id, d.title, d.description, d.severity, d.status,
                       d.project_id, p.name AS project_name,
                       r.full_name AS reported_by_name,
                       d.assigned_to, a.full_name AS assigned_to_name,
                       tr.test_name AS linked_test_name,
                       d.created_at
                FROM defects d
                LEFT JOIN projects p ON d.project_id = p.id
                LEFT JOIN users r ON d.reported_by = r.id
                LEFT JOIN users a ON d.assigned_to = a.id
                LEFT JOIN test_results tr ON d.test_result_id = tr.id
                {where}
                ORDER BY
                    CASE d.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                         WHEN 'medium' THEN 3 ELSE 4 END,
                    d.created_at DESC
                LIMIT :limit_val OFFSET :offset_val
                """
            ),
            params,
        ).fetchall()

        data = [
            DefectListItem(
                id=r[0], title=r[1], description=r[2],
                severity=r[3], status=r[4],
                project_id=r[5], project_name=r[6],
                reported_by_name=r[7], assigned_to=r[8], assigned_to_name=r[9],
                linked_test_name=r[10], created_at=r[11],
            )
            for r in rows
        ]

        logger.info("Defects listed by %s (count=%d)", current_user.id, len(data))
        return DefectListResponse(data=data, total=total, page=page, limit=limit)
    except Exception as exc:
        logger.error("Failed to list defects: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list defects",
        )


@router.get("/{defect_id}", response_model=DefectDetailResponse)
def get_defect(
    defect_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single defect detail. Developers only see their assigned defects."""
    try:
        where = "WHERE d.id = :defect_id"
        params: dict = {"defect_id": str(defect_id)}

        # Developer restriction
        if current_user.role == UserRole.developer:
            where += " AND d.assigned_to = :uid"
            params["uid"] = str(current_user.id)

        row = db.execute(
            text(
                f"""
                SELECT d.id, d.test_result_id, d.project_id, d.title, d.description,
                       d.severity, d.status, d.reported_by, d.assigned_to,
                       d.created_at, d.updated_at,
                       p.name AS project_name,
                       r.full_name AS reported_by_name,
                       a.full_name AS assigned_to_name,
                       tr.test_name AS linked_test_name
                FROM defects d
                LEFT JOIN projects p ON d.project_id = p.id
                LEFT JOIN users r ON d.reported_by = r.id
                LEFT JOIN users a ON d.assigned_to = a.id
                LEFT JOIN test_results tr ON d.test_result_id = tr.id
                {where}
                """
            ),
            params,
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Defect not found",
            )

        return DefectDetailResponse(
            id=row[0], test_result_id=row[1], project_id=row[2],
            title=row[3], description=row[4], severity=row[5],
            status=row[6], reported_by=row[7], assigned_to=row[8],
            created_at=row[9], updated_at=row[10],
            project_name=row[11], reported_by_name=row[12],
            assigned_to_name=row[13], linked_test_name=row[14],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get defect: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get defect",
        )


@router.patch("/{defect_id}", response_model=DefectResponse)
def update_defect(
    defect_id: UUID,
    body: DefectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("defects")),
):
    """Update a defect with RBAC-enforced field restrictions and status transitions."""
    try:
        # Fetch current defect. Explicit column list — do not use SELECT *:
        # project_id/reported_by were added later via ALTER TABLE ADD COLUMN
        # (migration 0005), which appends them at the end of the physical
        # column order in Postgres, not where they logically belong.
        row = db.execute(
            text(
                "SELECT id, test_result_id, project_id, reported_by, title, description, "
                "severity, status, assigned_to, created_at, updated_at "
                "FROM defects WHERE id = :did"
            ),
            {"did": str(defect_id)},
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Defect not found",
            )

        # Column order after SELECT *: id(0), test_result_id(1), project_id(2),
        # reported_by(3), title(4), description(5), severity(6), status(7),
        # assigned_to(8), created_at(9), updated_at(10)
        current_status = DefectStatus(row[7])  # status column
        current_severity = DefectSeverity(row[6])  # severity column
        current_assigned_to = row[8]  # assigned_to

        # RBAC field restrictions
        role = current_user.role

        if role == UserRole.developer:
            # Developer: can only update status
            if body.title is not None or body.description is not None or body.severity is not None or body.assigned_to is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Developers can only update the status field",
                )
        elif role == UserRole.qa_engineer:
            # QA Engineer: can update description and status only
            if body.title is not None or body.severity is not None or body.assigned_to is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="QA Engineers can only update description and status",
                )

        # Validate status transition if status is being changed
        if body.status is not None and body.status != current_status:
            _validate_transition(current_status, body.status)

        # Build update dynamically
        updates = []
        update_params: dict = {"did": str(defect_id)}

        if body.title is not None:
            updates.append("title = :title")
            update_params["title"] = body.title.strip()
        if body.description is not None:
            updates.append("description = :description")
            update_params["description"] = body.description
        if body.severity is not None:
            updates.append("severity = :severity")
            update_params["severity"] = body.severity.value
        if body.status is not None:
            updates.append("status = :status")
            update_params["status"] = body.status.value
        if body.assigned_to is not None:
            updates.append("assigned_to = :assigned_to")
            update_params["assigned_to"] = str(body.assigned_to)

        if not updates:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No fields to update",
            )

        updates.append("updated_at = NOW()")

        updated = db.execute(
            text(
                f"""
                UPDATE defects
                SET {', '.join(updates)}
                WHERE id = :did
                RETURNING id, test_result_id, project_id, title, description,
                          severity, status, reported_by, assigned_to,
                          created_at, updated_at
                """
            ),
            update_params,
        ).fetchone()
        db.commit()

        # Audit log
        write_audit_log(
            db,
            user_id=current_user.id,
            action="defect_updated",
            resource_type="defect",
            resource_id=str(defect_id),
            details={
                k: (v.value if hasattr(v, "value") else str(v) if v is not None else None)
                for k, v in body.model_dump(exclude_unset=True).items()
            },
        )

        logger.info("Defect updated: %s by %s", defect_id, current_user.id)
        return DefectResponse(
            id=updated[0], test_result_id=updated[1], project_id=updated[2],
            title=updated[3], description=updated[4], severity=updated[5],
            status=updated[6], reported_by=updated[7], assigned_to=updated[8],
            created_at=updated[9], updated_at=updated[10],
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Failed to update defect: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update defect",
        )
