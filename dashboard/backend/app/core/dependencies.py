"""FastAPI dependencies for DB sessions, current user, and RBAC."""
from typing import Generator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.models.user import User, UserRole

logger = get_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=True)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure it is closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve and return the authenticated user from a JWT access token."""
    payload = decode_access_token(token)
    sub = payload.get("sub")
    try:
        user_id = UUID(str(sub))
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid subject in token: %r", sub)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None:
        logger.warning("Authenticated user not found: %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.warning("Authenticated user is inactive: %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )
    return user


def require_roles(*roles: UserRole):
    """Dependency factory enforcing that the current user has an allowed role.

    Usage:
        @router.post("/users", dependencies=[Depends(require_roles(UserRole.admin))])
    or as a parameter to receive the user:
        user: User = Depends(require_roles(UserRole.admin))
    """
    allowed = set(roles)

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            logger.warning(
                "RBAC denied: user %s (%s) needs one of %s",
                current_user.id,
                current_user.role.value,
                [r.value for r in allowed],
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return _checker
