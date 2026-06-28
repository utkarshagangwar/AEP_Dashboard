"""Test suite management routes: CRUD nested under projects with RBAC."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_roles
from app.core.logging import get_logger
from app.models.project import Project
from app.models.test_suite import TestSuite
from app.models.user import User, UserRole
from app.schemas.test_suite import SuiteCreate, SuiteResponse, SuiteUpdate
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/suites", tags=["test-suites"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _get_active_project(db: Session, project_id: UUID) -> Project:
    """Fetch an active project or raise 404."""
    project = db.get(Project, project_id)
    if project is None or not project.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.post("", response_model=SuiteResponse, status_code=status.HTTP_201_CREATED)
def create_suite(
    project_id: UUID,
    payload: SuiteCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Create a test suite under a project (Admin, QA Lead only)."""
    try:
        _get_active_project(db, project_id)

        suite = TestSuite(
            name=payload.name.strip(),
            suite_type=payload.suite_type,
            description=payload.description,
            project_id=project_id,
            created_by=current_user.id,
        )
        db.add(suite)
        db.commit()
        db.refresh(suite)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="create_test_suite",
            resource_type="test_suite",
            resource_id=str(suite.id),
            details={"name": suite.name, "project_id": str(project_id)},
            ip_address=_client_ip(request),
        )

        logger.info("Test suite created: %s by %s", suite.id, current_user.id)
        return suite
    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.error("Create suite integrity error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A suite with this name may already exist in this project",
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("Create suite DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.get("", response_model=list[SuiteResponse])
def list_suites(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all active suites for a project."""
    try:
        _get_active_project(db, project_id)

        suites = (
            db.query(TestSuite)
            .filter(TestSuite.project_id == project_id, TestSuite.is_active.is_(True))
            .order_by(TestSuite.created_at.desc())
            .all()
        )

        logger.info(
            "Suites listed for project %s by %s (count=%d)",
            project_id,
            current_user.id,
            len(suites),
        )
        return suites
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("List suites DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.patch("/{suite_id}", response_model=SuiteResponse)
def update_suite(
    project_id: UUID,
    suite_id: UUID,
    payload: SuiteUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Update a test suite (Admin, QA Lead only)."""
    try:
        _get_active_project(db, project_id)

        suite = db.query(TestSuite).filter(
            TestSuite.id == suite_id,
            TestSuite.project_id == project_id,
            TestSuite.is_active.is_(True),
        ).first()

        if suite is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test suite not found",
            )

        if payload.name is None and payload.suite_type is None and payload.description is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No fields to update",
            )

        if payload.name is not None:
            suite.name = payload.name.strip()
        if payload.suite_type is not None:
            suite.suite_type = payload.suite_type
        if payload.description is not None:
            suite.description = payload.description

        db.commit()
        db.refresh(suite)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="update_test_suite",
            resource_type="test_suite",
            resource_id=str(suite.id),
            details=payload.model_dump(exclude_none=True, mode="json"),
            ip_address=_client_ip(request),
        )

        logger.info("Test suite updated: %s by %s", suite_id, current_user.id)
        return suite
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Update suite DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.delete("/{suite_id}", status_code=status.HTTP_200_OK)
def delete_suite(
    project_id: UUID,
    suite_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Soft-delete a test suite (Admin only). Sets is_active = false."""
    try:
        _get_active_project(db, project_id)

        suite = db.query(TestSuite).filter(
            TestSuite.id == suite_id,
            TestSuite.project_id == project_id,
            TestSuite.is_active.is_(True),
        ).first()

        if suite is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test suite not found",
            )

        suite.is_active = False
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_test_suite",
            resource_type="test_suite",
            resource_id=str(suite.id),
            details={"name": suite.name},
            ip_address=_client_ip(request),
        )

        logger.info("Test suite soft-deleted: %s by %s", suite_id, current_user.id)
        return {"message": "Test suite deleted successfully"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Delete suite DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
