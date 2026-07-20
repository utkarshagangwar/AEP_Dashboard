"""FastAPI application entrypoint for the Automation Execution Platform."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.core.seed import seed_initial_admin

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks: configure logging and seed the initial admin."""
    logger.info("Starting Automation Execution Platform API")
    if not os.environ.get("AI_CREDENTIAL_KEY"):
        logger.warning(
            "AI_CREDENTIAL_KEY is not set — AI credential profiles (Vibe Testing saved "
            "logins) will be encrypted with a key generated fresh on every restart and "
            "become permanently undecryptable the moment this process restarts. Set "
            "AI_CREDENTIAL_KEY to a stable value before anyone saves a credential profile "
            "in production. See .env.example for how to generate one."
        )
    seed_initial_admin()
    yield
    logger.info("Shutting down Automation Execution Platform API")


app = FastAPI(
    title="Automation Execution Platform API",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Prerequisite for eventually letting the browser call this API directly
# (bypassing the Next.js proxy route) — see .env.example for why the frontend
# half isn't wired up yet. CORS_ALLOWED_ORIGINS is empty by default, which
# means this changes nothing: same-origin/proxied requests never carry a
# browser Origin header in the first place, so this middleware has nothing to
# do with them either way. Only actual cross-origin browser requests are
# affected, and only once an origin is explicitly listed here.
_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
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
