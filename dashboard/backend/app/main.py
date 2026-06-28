"""FastAPI application entrypoint for the Automation Execution Platform."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.core.seed import seed_initial_admin

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks: configure logging and seed the initial admin."""
    logger.info("Starting Automation Execution Platform API")
    seed_initial_admin()
    yield
    logger.info("Shutting down Automation Execution Platform API")


app = FastAPI(
    title="Automation Execution Platform API",
    version="0.2.0",
    lifespan=lifespan,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# SlowAPIMiddleware applies the default limit to ALL routes automatically.
# Auth routes override with stricter @limiter.limit("10/minute") decorators.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Simple liveness probe."""
    return {"status": "ok", "version": "1.0.0"}
