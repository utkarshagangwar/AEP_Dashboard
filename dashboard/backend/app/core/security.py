"""
Security primitives: password hashing, JWT access tokens, refresh tokens.

Spec:
  - hash_password(plain) -> bcrypt hashed string
  - verify_password(plain, hashed) -> bool
  - create_access_token(data, expires_delta) -> JWT string
  - create_refresh_token(user_id) -> random 64-char hex token
  - decode_access_token(token) -> payload dict or raise HTTP 401
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── Password hashing ─────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if the plaintext password matches the bcrypt hash."""
    try:
        return _pwd_context.verify(plain, hashed)
    except ValueError:
        # Malformed/unknown hash format
        logger.warning("Password verification failed due to malformed hash")
        return False


# ─── Token helpers ────────────────────────────────────────────────────────────
def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token from the given claims."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(user_id: str) -> str:
    """Create an opaque random 64-character hex refresh token.

    The `user_id` argument is accepted to match the spec's signature and to
    allow logging/association by the caller; the token itself is random.
    """
    logger.debug("Creating refresh token for user %s", user_id)
    return secrets.token_hex(32)  # 32 bytes -> 64 hex chars


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token.

    Returns the payload dict, or raises HTTP 401 if invalid/expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        logger.warning("Access token decode failed: %s", exc)
        raise credentials_exception from exc

    if payload.get("type") != "access":
        logger.warning("Token presented is not an access token")
        raise credentials_exception

    if payload.get("sub") is None:
        logger.warning("Access token missing subject (sub) claim")
        raise credentials_exception

    return payload
