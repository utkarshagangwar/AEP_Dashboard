"""
Application configuration.

All secrets and tunables are loaded from environment variables via
pydantic-settings. Never hardcode secrets — provide them through the
environment or a local .env file (see .env.example).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from the environment."""

    # ─── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ─── JWT / Security ────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ─── Initial admin seed ────────────────────────────────────────────────────
    FIRST_ADMIN_EMAIL: str
    FIRST_ADMIN_PASSWORD: str

    # ─── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # ─── Automation ───────────────────────────────────────────────────────────
    AUTOMATION_ROOT: str = ""

    # ─── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
