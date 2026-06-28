"""Startup seeding: create the first Admin user if no users exist."""
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.user import User, UserRole

logger = get_logger(__name__)


def seed_initial_admin() -> None:
    """Create the initial admin from env vars if the users table is empty."""
    db = SessionLocal()
    try:
        user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
        if user_count and user_count > 0:
            logger.info("Seed skipped: %s user(s) already exist", user_count)
            return

        email = settings.FIRST_ADMIN_EMAIL.lower().strip()
        admin = User(
            email=email,
            hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
            full_name="System Admin",
            role=UserRole.admin,
        )
        db.add(admin)
        db.commit()
        logger.info("Initial admin user seeded: %s", email)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to seed initial admin: %s", exc)
        db.rollback()
    finally:
        db.close()
