# ─────────────────────────────────────────────────────────────────────────────
# AEP Dashboard — Backend Docker Image
# Base: Python 3.11-slim (Debian Bookworm)
# Baked in: FastAPI deps, Robot Framework, Playwright + Chromium, Node.js
#
# WHY THIS ORDER:
#   1. System deps first — they change least often, so Docker reuses this
#      layer across almost every rebuild.
#   2. Node.js next — same reason.
#   3. Python deps before copying app code — requirements.txt changes less
#      often than source files, so pip install stays cached between code pushes.
#   4. App code last — most frequently changed, should invalidate fewest layers.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# ── 1. System packages ────────────────────────────────────────────────────────
# Playwright/Chromium needs a set of shared libs that aren't in slim.
# curl is needed to bootstrap Node.js.
# ca-certificates covers HTTPS inside the container.
# We clean apt cache in the same RUN layer to keep image size down.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    # Chromium OS dependencies (required by Playwright's bundled Chromium)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxshmfence1 \
    fonts-liberation \
    libappindicator3-1 \
    xdg-utils \
    wget \
 && rm -rf /var/lib/apt/lists/*

# ── 2. Node.js (LTS) ─────────────────────────────────────────────────────────
# rfbrowser init requires Node.js to be present.
# Using NodeSource's official LTS (v20) distribution.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# ── 3. Python dependencies ────────────────────────────────────────────────────
# Copy requirements first — pip install runs only when these files change,
# not on every source code change.
WORKDIR /app

COPY dashboard/backend/requirements.txt ./requirements.txt

# requirements-robot.txt is optional — if your repo has it, it installs
# Robot Framework + SeleniumLibrary + browser-specific deps.
# If the file doesn't exist, the second pip install is a no-op.
COPY dashboard/backend/requirements-robot.txt* ./

RUN pip install --no-cache-dir -r requirements.txt \
 && if [ -f requirements-robot.txt ]; then \
        pip install --no-cache-dir -r requirements-robot.txt; \
    fi

# ── 4. Playwright browser (Chromium only) ─────────────────────────────────────
# Install Playwright Python package if not already in requirements.
# Then download only Chromium — skipping Firefox and WebKit keeps the image
# smaller (Chromium alone is ~300 MB vs ~900 MB for all browsers).
# PLAYWRIGHT_BROWSERS_PATH tells Playwright where to store/find the binary.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright \
 && playwright install chromium

# ── 5. rfbrowser init (Robot Framework Browser library) ──────────────────────
# Only runs if robotframework-browser is installed in requirements-robot.txt.
# rfbrowser init downloads the Node.js side of the Browser library.
RUN python -m pip show robotframework-browser > /dev/null 2>&1 \
 && rfbrowser init || true

# ── 6. Copy application code ──────────────────────────────────────────────────
# Copying AFTER dependency installation keeps the pip-install layer cached
# when only source files change.
COPY dashboard/backend/ ./

# Copy automation test suites into /automation inside the container.
# Celery workers run robot tests from this path.
COPY automation/ /automation/
ENV AUTOMATION_ROOT=/automation

# ── 7. Runtime config ─────────────────────────────────────────────────────────
# start.sh handles: alembic migrations → Celery (background) → uvicorn (foreground)
COPY dashboard/backend/start.sh /start.sh
RUN chmod +x /start.sh

# Non-root user for security — Render runs containers as root by default,
# but this is the correct production posture.
# Comment out if Playwright has permission issues finding its browser binary.
# RUN useradd -m -u 1001 appuser && chown -R appuser /app /automation /ms-playwright
# USER appuser

EXPOSE 8000

CMD ["/start.sh"]
