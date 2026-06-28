"""Authentication business logic.

Handles user authentication, refresh-token issuance/persistence, validation,
rotation, and revocation. Refresh tokens are opaque random strings; only their
SHA-256 hash is stored in the database.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User

logger = get_logger(__name__)


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw refresh token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Return the user if credentials are valid and the account is active."""
    normalized = email.lower().strip()
    user = db.execute(
        select(User).where(User.email == normalized)
    ).scalar_one_or_none()

    if user is None:
        logger.warning("Login failed: user not found (%s)", normalized)
        return None
    if not user.is_active:
        logger.warning("Login failed: account inactive (%s)", normalized)
        return None
    if not verify_password(password, user.hashed_password):
        logger.warning("Login failed: invalid password (%s)", normalized)
        return None
    return user


def issue_tokens(db: Session, user: User) -> tuple[str, str]:
    """Create an access token and a persisted refresh token for the user.

    Returns (access_token, raw_refresh_token). Only the hash of the refresh
    token is stored.
    """
    access_token = create_access_token(
        {"sub": str(user.id), "email": user.email, "role": user.role.value}
    )
    raw_refresh = create_refresh_token(str(user.id))
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    db.add(
        RefreshToken(
            user_id=user.id,
            token=_hash_token(raw_refresh),
            expires_at=expires_at,
        )
    )
    db.commit()
    return access_token, raw_refresh


def validate_refresh_token(
    db: Session, raw_token: str
) -> tuple[Optional[User], Optional[RefreshToken]]:
    """Validate a refresh token against the DB.

    Returns (user, token_row) if valid; otherwise (None, None).
    """
    token_hash = _hash_token(raw_token)
    token_row = db.execute(
        select(RefreshToken).where(RefreshToken.token == token_hash)
    ).scalar_one_or_none()

    if token_row is None:
        logger.warning("Refresh token not found")
        return None, None
    if token_row.is_revoked:
        logger.warning("Refresh token already revoked (user=%s)", token_row.user_id)
        return None, None

    expires_at = token_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        logger.warning("Refresh token expired (user=%s)", token_row.user_id)
        return None, None

    user = db.get(User, token_row.user_id)
    if user is None or not user.is_active:
        logger.warning("Refresh token user missing/inactive")
        return None, None

    return user, token_row


def rotate_refresh_token(
    db: Session, user: User, old_token_row: RefreshToken
) -> tuple[str, str]:
    """Revoke the old refresh token and issue a fresh access/refresh pair."""
    old_token_row.is_revoked = True
    db.add(old_token_row)
    db.commit()
    return issue_tokens(db, user)


def revoke_refresh_token(db: Session, user_id: UUID, raw_token: str) -> bool:
    """Revoke a specific refresh token belonging to the user.

    Returns True if a matching token was revoked.
    """
    token_hash = _hash_token(raw_token)
    token_row = db.execute(
        select(RefreshToken).where(
            RefreshToken.token == token_hash,
            RefreshToken.user_id == user_id,
        )
    ).scalar_one_or_none()

    if token_row is None:
        return False

    token_row.is_revoked = True
    db.add(token_row)
    db.commit()
    logger.info("Refresh token revoked (user=%s)", user_id)
    return True
