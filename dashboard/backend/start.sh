#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AEP Dashboard — Container Startup Script
#
# Runs at container start (not at image build time).
# Sequence matters:
#   1. Migrations first — DB must be ready before the app or workers start.
#   2. Celery in background — needs the app code but not the API to be up yet.
#   3. uvicorn in foreground — process manager (Render) tracks this PID.
#      If uvicorn exits, the container exits and Render restarts it.
#
# WHY NOT migrate at build time:
#   Docker image builds have no access to your Neon DB. The DATABASE_URL env
#   var isn't injected until the container runs. Running alembic upgrade head
#   at build time causes a "could not connect to server" failure every time.
# ─────────────────────────────────────────────────────────────────────────────

set -e  # exit immediately if any command fails

echo "[startup] === AEP Backend starting ==="
echo "[startup] Python: $(python --version)"
echo "[startup] Working dir: $(pwd)"

# ── Step 1: Database migrations ───────────────────────────────────────────────
# Wait briefly for the DB to be ready (Render sometimes starts the container
# before Neon connection pooler is fully warmed up).
echo "[startup] Running database migrations..."
python -c "
import time, psycopg2, os, sys
url = os.environ.get('DATABASE_URL', '')
if not url:
    print('[startup] WARNING: DATABASE_URL not set — skipping connection check')
    sys.exit(0)
for attempt in range(10):
    try:
        conn = psycopg2.connect(url)
        conn.close()
        print(f'[startup] DB reachable after {attempt+1} attempt(s)')
        sys.exit(0)
    except Exception as e:
        print(f'[startup] DB not ready ({e}), retrying in 3s...')
        time.sleep(3)
print('[startup] ERROR: DB not reachable after 10 attempts')
sys.exit(1)
"

alembic upgrade head
echo "[startup] Migrations complete"

# ── Step 2: Celery worker (background) ────────────────────────────────────────
# --pool=solo      → single-threaded, no forking — safest on 512 MB RAM
# --concurrency=1  → one task at a time (Chromium is memory-heavy)
# --loglevel=info  → logs task start/end/error in Render's log stream
# -Q celery        → default queue; add more queues here if you split them later
#
# & puts it in background; the PID is captured so we can monitor it.
echo "[startup] Starting Celery worker..."
celery -A app.workers.celery_app:celery_app worker \
    --pool=solo \
    --concurrency=1 \
    --loglevel=info \
    -Q celery &
CELERY_PID=$!
echo "[startup] Celery PID: $CELERY_PID"

# Brief pause — give Celery time to connect to Redis before API receives
# requests that might trigger task dispatch.
sleep 2

# Optional: verify Celery actually started (exits non-zero if process died)
if ! kill -0 $CELERY_PID 2>/dev/null; then
    echo "[startup] ERROR: Celery worker failed to start"
    # Don't exit — API still works without Celery; test execution will fail
    # gracefully, but dashboard CRUD/auth/reports are unaffected.
fi

# ── Step 3: FastAPI (foreground) ──────────────────────────────────────────────
# $PORT is injected by Render. Never hardcode a port.
# --host 0.0.0.0 is required — localhost won't accept Render's proxy traffic.
# --workers 1 keeps RAM usage predictable on the free tier.
echo "[startup] Starting FastAPI on port ${PORT:-8000}..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --log-level info
