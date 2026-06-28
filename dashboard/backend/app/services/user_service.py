"""User management business logic."""
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserUpdate

logger = get_logger(__name__)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    normalized = email.lower().strip()
    return db.execute(
        select(User).where(User.email == normalized)
    ).scalar_one_or_none()


def create_user(db: Session, payload: UserCreate) -> User:
    """Create a new user with a hashed password."""
    user = User(
        email=payload.email.lower().strip(),
        hashed_password=hash_password(payload.password),
        full_name=(payload.full_name or payload.email.split("@")[0]).strip(),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("User created: %s (role=%s)", user.email, user.role.value)
    return user


def list_users(db: Session, page: int, limit: int) -> tuple[list[User], int]:
    """Return a page of users and the total count."""
    offset = (page - 1) * limit
    total = db.execute(select(func.count()).select_from(User)).scalar_one()
    users = (
        db.execute(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return list(users), int(total)


def get_user(db: Session, user_id: UUID) -> Optional[User]:
    return db.get(User, user_id)


def update_user(db: Session, user: User, payload: UserUpdate) -> User:
    """Update a user's role and/or active status."""
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("User updated: %s", user.id)
    return user


def ensure_valid_role(value: str) -> UserRole:
    """Coerce/validate a role string to a UserRole (raises ValueError)."""
    return UserRole(value)
