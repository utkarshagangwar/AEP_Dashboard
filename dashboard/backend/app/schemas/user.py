"""Pydantic schemas for user management endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    full_name: str = Field(default="", max_length=255)


class UserUpdate(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: UserRole
    is_active: bool
    full_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedUsers(BaseModel):
    data: list[UserOut]
    total: int
    page: int
    limit: int
