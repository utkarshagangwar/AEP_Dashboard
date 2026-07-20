"""Authentication routes: login, refresh, logout, me."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.core.rate_limit import limiter
from app.services import auth_service
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate a user and return access + refresh tokens."""
    logger.info("Login attempt: %s", payload.email)
    try:
        user = auth_service.authenticate_user(db, payload.email, payload.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        access_token, refresh_token = auth_service.issue_tokens(db, user)

        write_audit_log(
            db,
            user_id=user.id,
            action="login",
            resource_type="user",
            resource_id=str(user.id),
            ip_address=_client_ip(request),
        )

        logger.info("Login successful: %s (role=%s)", user.id, user.role.value)
        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            refresh_token=refresh_token,
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Login DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
def refresh(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    """Validate a refresh token and issue a new access+refresh pair (rotation).

    The new refresh token MUST be returned here — rotation revokes the old
    one server-side, so if the caller never learns the new value (this used
    to discard it into `_new_refresh` and return only the access token),
    every refresh after the very first one presents an already-revoked
    token and fails. The frontend proxy (/api/auth/refresh/route.js) is what
    actually turns this into the rotated httpOnly cookie — it can only do
    that if this response carries the new value.
    """
    try:
        user, token_row = auth_service.validate_refresh_token(
            db, payload.refresh_token
        )
        if user is None or token_row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        access_token, new_refresh_token = auth_service.rotate_refresh_token(
            db, user, token_row
        )
        logger.info("Access token refreshed: %s", user.id)
        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            refresh_token=new_refresh_token,
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Refresh DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.post("/logout")
@limiter.limit("10/minute")
def logout(request: Request,
    payload: LogoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke the provided refresh token for the authenticated user."""
    try:
        auth_service.revoke_refresh_token(db, current_user.id, payload.refresh_token)
        write_audit_log(
            db,
            user_id=current_user.id,
            action="logout",
            resource_type="user",
            resource_id=str(current_user.id),
        )
        logger.info("Logout: %s", current_user.id)
        return {"message": "Logged out successfully"}
    except SQLAlchemyError as exc:
        logger.error("Logout DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    logger.debug("Me endpoint accessed: %s", current_user.id)
    return current_user
