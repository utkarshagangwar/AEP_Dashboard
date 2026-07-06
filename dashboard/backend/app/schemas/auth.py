"""Pydantic schemas for authentication endpoints."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.user import UserRole


class LoginRequest(BaseModel):
    # Use str (not EmailStr) — login is a DB lookup, not a registration form.
    # email-validator 2.x rejects special-use TLDs like .local, which are
    # valid for internal/seeded admin accounts.
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class MeResponse(BaseModel):
    id: UUID
    # str (not EmailStr) — same reason as LoginRequest above: this reflects
    # already-stored data back to the client, it doesn't need re-validation,
    # and email-validator 2.x would 500 on seeded .local admin accounts.
    email: str
    full_name: str
    role: UserRole
    # Explicit feature-access grants (see app/core/permissions.py) — the
    # frontend uses this to show/hide nav items. Empty for non-admins
    # unless explicitly granted; admins effectively have all of them but
    # this list is not populated for admins (they bypass checks by role).
    permissions: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}
