"""
Centralised logging configuration using the standard `logging` module.

Call `configure_logging()` once at application startup, then obtain
module-level loggers via `get_logger(__name__)`.
"""
import logging
import sys

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_configured = False


def configure_logging() -> None:
    """Configure the root logger. Idempotent."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if reconfigured (e.g. uvicorn reload)
    root.handlers.clear()
    root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring logging is configured."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)
