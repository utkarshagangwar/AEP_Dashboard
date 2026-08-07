"""Project management routes: CRUD operations with RBAC."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission, require_roles
from app.core.logging import get_logger
from app.models.ai_runs import AICredentialProfile
from app.models.project import Project, ProjectEnvironment
from app.models.test_suite import TestSuite
from app.models.user import User, UserRole
from app.schemas.project import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectEnvironmentResponse,
    ProjectEnvironmentUpsert,
    ProjectListResponse,
    ProjectResponse,
    ProjectTestSetup,
    ProjectTestSetupResponse,
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
        if payload.environments is not None:
            project.environments = payload.environments
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
            environments=project.environments,
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
                environments=p.environments,
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
            environments=project.environments,
            suite_count=suite_count or 0,
            created_at=project.created_at,
            updated_at=project.updated_at,
            suites=[
                SuiteSummary(
                    id=s.id,
                    name=s.name,
                    suite_type=s.suite_type.value if s.suite_type else None,
                    description=s.description,
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

        if (
            payload.name is None
            and payload.description is None
            and payload.is_active is None
            and payload.environments is None
        ):
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

        if payload.environments is not None:
            project.environments = payload.environments

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
            environments=project.environments,
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


# ── Project environments ────────────────────────────────────────────────────
#
# Gives a project's environment labels ("dev", "staging", "production") a
# real address. Without this, `Project.environments` was a list of names
# with nothing behind them, and any run scoped only to a project — notably
# a prompt skill extracted from a SOW, which is saved under a project but
# never given a credential profile at parse time — resolved no URL,
# received environment_url="about:blank", and opened a blank tab.
#
# Read is gated on the same "projects" permission as the rest of this
# router. Write is Admin/QA-Lead only via require_permission("projects"),
# matching create/update_project: pointing a project's environment at a
# different host silently redirects every future test run for that project,
# which is not a change an ordinary runner should be able to make.


@router.get(
    "/{project_id}/environments",
    response_model=list[ProjectEnvironmentResponse],
)
def list_project_environments(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List every configured environment address for a project."""
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

        rows = (
            db.query(ProjectEnvironment)
            .filter(ProjectEnvironment.project_id == project_id)
            .order_by(ProjectEnvironment.environment)
            .all()
        )

        # Resolve profile names in one pass rather than per row — the
        # settings UI renders the whole list at once and an N+1 here would
        # scale with a project's environment count for no reason.
        profile_names: dict = {}
        wanted = {r.default_credential_profile_id for r in rows if r.default_credential_profile_id}
        if wanted:
            profile_names = {
                p.id: p.name
                for p in db.query(AICredentialProfile)
                .filter(AICredentialProfile.id.in_(wanted))
                .all()
            }

        return [
            ProjectEnvironmentResponse(
                id=r.id,
                project_id=r.project_id,
                environment=r.environment,
                base_url=r.base_url,
                default_credential_profile_id=r.default_credential_profile_id,
                default_credential_profile_name=profile_names.get(
                    r.default_credential_profile_id
                ),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("List project environments DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.put(
    "/{project_id}/environments",
    response_model=ProjectEnvironmentResponse,
)
def upsert_project_environment(
    project_id: UUID,
    payload: ProjectEnvironmentUpsert,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Create or update one environment's address and default login.

    PUT rather than POST because it is idempotent on
    (project_id, environment) — the unique constraint added in migration
    0041 — so the settings UI can save the same row repeatedly without
    accumulating duplicates or needing to track row ids.
    """
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

        # Validate the referenced profile exists before writing. A dangling
        # default would surface much later as a confusing "no credentials"
        # failure mid-run rather than an error at the point of the mistake.
        if payload.default_credential_profile_id:
            profile = db.get(AICredentialProfile, payload.default_credential_profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Credential profile not found",
                )

        environment = payload.environment.strip()

        row = (
            db.query(ProjectEnvironment)
            .filter(
                ProjectEnvironment.project_id == project_id,
                ProjectEnvironment.environment == environment,
            )
            .one_or_none()
        )
        created = row is None
        if row is None:
            row = ProjectEnvironment(project_id=project_id, environment=environment)
            db.add(row)

        row.base_url = payload.base_url
        row.default_credential_profile_id = payload.default_credential_profile_id

        db.commit()
        db.refresh(row)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="upsert_project_environment",
            resource_type="project",
            resource_id=str(project_id),
            details={
                "environment": environment,
                "base_url": row.base_url,
                "created": created,
                "default_credential_profile_id": (
                    str(row.default_credential_profile_id)
                    if row.default_credential_profile_id
                    else None
                ),
            },
            ip_address=_client_ip(request),
        )

        logger.info(
            "Project %s environment '%s' %s by %s (base_url=%s)",
            project_id,
            environment,
            "created" if created else "updated",
            current_user.id,
            row.base_url,
        )

        profile_name = None
        if row.default_credential_profile_id:
            prof = db.get(AICredentialProfile, row.default_credential_profile_id)
            profile_name = getattr(prof, "name", None)

        return ProjectEnvironmentResponse(
            id=row.id,
            project_id=row.project_id,
            environment=row.environment,
            base_url=row.base_url,
            default_credential_profile_id=row.default_credential_profile_id,
            default_credential_profile_name=profile_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Upsert project environment DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.delete(
    "/{project_id}/environments/{environment}",
    status_code=status.HTTP_200_OK,
)
def delete_project_environment(
    project_id: UUID,
    environment: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Remove one environment's configured address.

    Hard delete, unlike projects themselves: this row is pure
    configuration with no history or audit value of its own beyond the
    audit log entry written here, and a soft-deleted address would have to
    be filtered out of the resolver's lookup anyway.
    """
    try:
        row = (
            db.query(ProjectEnvironment)
            .filter(
                ProjectEnvironment.project_id == project_id,
                ProjectEnvironment.environment == environment,
            )
            .one_or_none()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project environment not found",
            )

        db.delete(row)
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_project_environment",
            resource_type="project",
            resource_id=str(project_id),
            details={"environment": environment},
            ip_address=_client_ip(request),
        )

        logger.info(
            "Project %s environment '%s' deleted by %s",
            project_id,
            environment,
            current_user.id,
        )
        return {"message": "Environment removed successfully"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Delete project environment DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── Test setup (single-screen configuration) ────────────────────────────────
#
# Replaces the two-step "pick an environment, then save a row" flow with
# one decision: which login. The start URL follows from it, because a
# kind="bypass" profile already carries its own logged-in landing URL and
# asking for a base URL alongside it created two competing sources of
# truth for the same thing.
#
# The default login lives on the PROJECT (migration 0042), not on an
# environment row. Coupling it to an environment-label match meant a
# SOW-extracted skill — which has environment = NULL — silently lost its
# login on any lookup miss and ran unauthenticated into a CAPTCHA.


def _resolve_setup_view(db, project) -> ProjectTestSetupResponse:
    """Build the Test setup screen's view of a project.

    Reuses app.services.start_context so the URL shown to the user is the
    one the worker will genuinely use — not a second, drifting
    reimplementation of the same precedence rules.
    """
    from app.services.start_context import resolve_start_context

    profile = None
    if project.default_credential_profile_id:
        profile = db.get(AICredentialProfile, project.default_credential_profile_id)

    rows = (
        db.query(ProjectEnvironment)
        .filter(ProjectEnvironment.project_id == project.id)
        .order_by(ProjectEnvironment.environment)
        .all()
    )

    # environment=None asks the resolver for the project-wide answer, which
    # is what a SOW-extracted skill (environment = NULL) will get.
    ctx = resolve_start_context(db, project_id=project.id, environment=None)

    reason = None
    if not ctx.has_url:
        reason = (
            "No start URL yet. Choose a login that has a target URL, or set a "
            "start URL under Advanced."
        )

    return ProjectTestSetupResponse(
        project_id=project.id,
        default_credential_profile_id=project.default_credential_profile_id,
        default_credential_profile_name=getattr(profile, "name", None),
        default_credential_profile_kind=getattr(profile, "kind", None),
        start_url=ctx.environment_url if ctx.has_url else None,
        start_url_source=ctx.url_source,
        is_ready=ctx.has_url,
        reason=reason,
        environments=[
            ProjectEnvironmentResponse(
                id=r.id,
                project_id=r.project_id,
                environment=r.environment,
                base_url=r.base_url,
                default_credential_profile_id=r.default_credential_profile_id,
                default_credential_profile_name=None,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ],
    )


@router.get("/{project_id}/test-setup", response_model=ProjectTestSetupResponse)
def get_project_test_setup(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Everything the Test setup popup needs, in one request."""
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        return _resolve_setup_view(db, project)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Get project test setup DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.put("/{project_id}/test-setup", response_model=ProjectTestSetupResponse)
def save_project_test_setup(
    project_id: UUID,
    payload: ProjectTestSetup,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("projects")),
):
    """Save the whole Test setup screen in one call.

    Admin/QA-Lead only, same as create/update_project: changing a
    project's login or start URL silently redirects every future test run
    for that project, which is not a change an ordinary runner should be
    able to make.
    """
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

        # Validate before writing — a dangling default would otherwise
        # surface much later as a confusing mid-run "no credentials"
        # failure rather than an error at the point of the mistake.
        if payload.default_credential_profile_id:
            profile = db.get(AICredentialProfile, payload.default_credential_profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Credential profile not found",
                )

        project.default_credential_profile_id = payload.default_credential_profile_id

        # The URL half is optional and only used when the login carries no
        # target_url of its own, or when overridden under Advanced.
        if payload.environment and payload.base_url is not None:
            environment = payload.environment.strip()
            row = (
                db.query(ProjectEnvironment)
                .filter(
                    ProjectEnvironment.project_id == project_id,
                    ProjectEnvironment.environment == environment,
                )
                .one_or_none()
            )
            if row is None:
                row = ProjectEnvironment(project_id=project_id, environment=environment)
                db.add(row)
            row.base_url = payload.base_url

        db.commit()
        db.refresh(project)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="save_project_test_setup",
            resource_type="project",
            resource_id=str(project_id),
            details={
                "default_credential_profile_id": (
                    str(payload.default_credential_profile_id)
                    if payload.default_credential_profile_id
                    else None
                ),
                "environment": payload.environment,
                "base_url": payload.base_url,
            },
            ip_address=_client_ip(request),
        )

        logger.info(
            "Project %s test setup saved by %s (login=%s, env=%s)",
            project_id,
            current_user.id,
            payload.default_credential_profile_id,
            payload.environment,
        )

        return _resolve_setup_view(db, project)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Save project test setup DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
