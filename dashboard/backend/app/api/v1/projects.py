"""Project management routes: CRUD operations with RBAC."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission, require_roles
from app.core.logging import get_logger
from app.models.project import Project
from app.models.test_suite import TestSuite
from app.models.user import User, UserRole
from app.schemas.project import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    SuiteSummary,
)
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/discover-suites", status_code=status.HTTP_200_OK)
def discover_suites(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Scan automation folder and auto-register test suites."""
    from app.services.suite_discovery import discover_and_register_suites

    return discover_and_register_suites(db)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Create a new project (Admin, QA Lead only)."""
    try:
        # Check uniqueness
        existing = (
            db.query(Project)
            .filter(Project.name == payload.name, Project.is_active.is_(True))
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A project with this name already exists",
            )

        project = Project(name=payload.name.strip(), description=payload.description)
        db.add(project)
        db.commit()
        db.refresh(project)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="create_project",
            resource_type="project",
            resource_id=str(project.id),
            details={"name": project.name},
            ip_address=_client_ip(request),
        )

        logger.info("Project created: %s by %s", project.id, current_user.id)
        return ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            is_active=project.is_active,
            suite_count=0,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.error("Create project integrity error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A project with this name already exists",
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("Create project DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.get("", response_model=ProjectListResponse)
def list_projects(
    search: Optional[str] = Query(
        None, description="Case-insensitive filter on project name/description"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return active projects with suite counts, optionally filtered by
    name/description (search) and capped/paginated (page, limit)."""
    try:
        filters = [Project.is_active.is_(True)]
        if search and search.strip():
            term = f"%{search.strip()}%"
            filters.append(or_(Project.name.ilike(term), Project.description.ilike(term)))

        total = db.query(func.count(Project.id)).filter(*filters).scalar() or 0

        suite_count_sq = (
            db.query(func.count(TestSuite.id))
            .filter(TestSuite.project_id == Project.id, TestSuite.is_active.is_(True))
            .correlate(Project)
            .scalar_subquery()
        )

        offset = (page - 1) * limit
        projects = (
            db.query(Project, suite_count_sq.label("suite_count"))
            .filter(*filters)
            .order_by(Project.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        data = [
            ProjectResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                is_active=p.is_active,
                suite_count=sc or 0,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p, sc in projects
        ]

        logger.info(
            "Projects listed by %s (count=%d, total=%d, search=%r)",
            current_user.id, len(data), total, search,
        )
        return ProjectListResponse(data=data, total=total, page=page, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("List projects DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return project detail with its test suites."""
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        suite_count = (
            db.query(func.count(TestSuite.id))
            .filter(TestSuite.project_id == project_id, TestSuite.is_active.is_(True))
            .scalar()
        )

        suites = (
            db.query(TestSuite)
            .filter(TestSuite.project_id == project_id, TestSuite.is_active.is_(True))
            .order_by(TestSuite.created_at.desc())
            .all()
        )

        logger.info("Project detail accessed: %s by %s", project_id, current_user.id)
        return ProjectDetailResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            is_active=project.is_active,
            suite_count=suite_count or 0,
            created_at=project.created_at,
            updated_at=project.updated_at,
            suites=[
                SuiteSummary(
                    id=s.id,
                    name=s.name,
                    suite_type=s.suite_type.value if s.suite_type else None,
                    is_active=s.is_active,
                    created_at=s.created_at,
                )
                for s in suites
            ],
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Get project DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Update a project (Admin, QA Lead only)."""
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        if payload.name is None and payload.description is None and payload.is_active is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No fields to update",
            )

        if payload.name is not None:
            # Check uniqueness if name is changing
            if payload.name.strip() != project.name:
                dup = (
                    db.query(Project)
                    .filter(Project.name == payload.name.strip(), Project.is_active.is_(True))
                    .first()
                )
                if dup is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="A project with this name already exists",
                    )
            project.name = payload.name.strip()

        if payload.description is not None:
            project.description = payload.description

        if payload.is_active is not None:
            project.is_active = payload.is_active

        db.commit()
        db.refresh(project)

        suite_count = (
            db.query(func.count(TestSuite.id))
            .filter(TestSuite.project_id == project_id, TestSuite.is_active.is_(True))
            .scalar()
        )

        write_audit_log(
            db,
            user_id=current_user.id,
            action="update_project",
            resource_type="project",
            resource_id=str(project.id),
            details=payload.model_dump(exclude_none=True, mode="json"),
            ip_address=_client_ip(request),
        )

        logger.info("Project updated: %s by %s", project_id, current_user.id)
        return ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            is_active=project.is_active,
            suite_count=suite_count or 0,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Update project DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
def delete_project(
    project_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
):
    """Soft-delete a project (Admin only). Sets is_active = false."""
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        project.is_active = False
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_project",
            resource_type="project",
            resource_id=str(project.id),
            details={"name": project.name},
            ip_address=_client_ip(request),
        )

        logger.info("Project soft-deleted: %s by %s", project_id, current_user.id)
        return {"message": "Project deleted successfully"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Delete project DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
