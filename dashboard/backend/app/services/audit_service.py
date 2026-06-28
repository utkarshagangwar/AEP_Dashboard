"""Audit logging service — writes records to the audit_logs table.

Audit failures must never crash the main request, so all errors are caught
and logged.
"""
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.audit_log import AuditLog

logger = get_logger(__name__)


def write_audit_log(
    db: Session,
    *,
    user_id: Optional[UUID],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Persist an audit log entry. Best-effort: never raises."""
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
        logger.debug("Audit log written: action=%s resource=%s", action, resource_type)
    except Exception as exc:  # noqa: BLE001 - audit must never break requests
        logger.error("Failed to write audit log (action=%s): %s", action, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
