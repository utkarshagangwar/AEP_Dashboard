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

    # Number of parallel pabot processes to use when executing a suite.
    # 1 (default) = current behavior exactly: plain `robot`, no pabot involved,
    # single browser, single process. Only raise this once you've confirmed the
    # host has the CPU/RAM headroom for that many concurrent headless browsers —
    # each pabot process opens and drives its own browser.
    PABOT_PROCESSES: int = 1

    # ─── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of origins allowed to call this API directly from
    # the browser (bypassing the Next.js proxy route). Empty by default — no
    # behavior change until you explicitly set this to your frontend's public
    # URL (e.g. https://your-frontend.onrender.com), which only matters if you
    # also point NEXT_PUBLIC_DIRECT_API_URL at this backend on the frontend
    # side. See .env.example.
    CORS_ALLOWED_ORIGINS: str = ""

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
