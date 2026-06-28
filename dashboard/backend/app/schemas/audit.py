"""Pydantic schemas for the audit log viewer endpoint."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    """A single audit log entry."""
    id: UUID
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    """Paginated audit log response."""
    data: list[AuditLogEntry]
    total: int
    page: int
    limit: int
