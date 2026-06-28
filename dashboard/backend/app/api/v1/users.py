"""User management routes (Admin only): create, list, update."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_roles
from app.core.logging import get_logger
from app.models.user import User, UserRole
from app.schemas.user import PaginatedUsers, UserCreate, UserOut, UserUpdate
from app.services import user_service
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# Admin-only guard for every route in this module.
AdminUser = Depends(require_roles(UserRole.admin))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = AdminUser,
):
    """Create a new user (Admin only)."""
    try:
        existing = user_service.get_user_by_email(db, payload.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use",
            )

        user = user_service.create_user(db, payload)
        write_audit_log(
            db,
            user_id=current_user.id,
            action="create_user",
            resource_type="user",
            resource_id=str(user.id),
            details={"email": user.email, "role": user.role.value},
        )
        return user
    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.error("Create user integrity error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already in use",
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("Create user DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.get("", response_model=PaginatedUsers)
def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = AdminUser,
):
    """Return a paginated list of all users (Admin only)."""
    try:
        users, total = user_service.list_users(db, page, limit)
        logger.info("Users listed by %s (count=%s)", current_user.id, len(users))
        return PaginatedUsers(data=users, total=total, page=page, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("List users DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = AdminUser,
):
    """Update a user's role and/or active status (Admin only)."""
    try:
        if payload.role is None and payload.is_active is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No fields to update",
            )

        user = user_service.get_user(db, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Prevent an admin from demoting or deactivating themselves.
        if user.id == current_user.id:
            if payload.role is not None and payload.role != UserRole.admin:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change your own role",
                )
            if payload.is_active is False:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot deactivate your own account",
                )

        updated = user_service.update_user(db, user, payload)
        write_audit_log(
            db,
            user_id=current_user.id,
            action="update_user",
            resource_type="user",
            resource_id=str(updated.id),
            details=payload.model_dump(exclude_none=True, mode="json"),
        )
        return updated
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Update user DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc
