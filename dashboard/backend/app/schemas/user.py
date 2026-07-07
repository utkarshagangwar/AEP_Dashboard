"""Pydantic schemas for user management endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.permissions import PERMISSION_KEYS
from app.models.user import UserRole


def _validate_permissions(value: list[str]) -> list[str]:
    unknown = sorted(set(value) - set(PERMISSION_KEYS))
    if unknown:
        raise ValueError(f"Unknown permission(s): {', '.join(unknown)}")
    return value


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    full_name: str = Field(default="", max_length=255)
    # Explicit feature access granted at creation time (see
    # app/core/permissions.py) — role carries no implicit access.
    permissions: list[str] = Field(default_factory=list)

    _validate_permissions = field_validator("permissions")(_validate_permissions)


class UserUpdate(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    permissions: Optional[list[str]] = None

    _validate_permissions = field_validator("permissions")(
        lambda v: v if v is None else _validate_permissions(v)
    )


class UserOut(BaseModel):
    id: UUID
    # str (not EmailStr) — output only, reflects already-stored data. Using
    # EmailStr here would re-validate on every read and 500 for any seeded
    # .local admin account, since email-validator 2.x rejects special-use
    # TLDs (same issue already worked around in schemas/auth.py).
    email: str
    role: UserRole
    is_active: bool
    full_name: str
    permissions: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedUsers(BaseModel):
    data: list[UserOut]
    total: int
    page: int
    limit: int


class UserBrief(BaseModel):
    """Minimal user info for pickers (e.g. defect assignment) — no email/permissions."""

    id: UUID
    full_name: str
    role: UserRole

    model_config = {"from_attributes": True}
