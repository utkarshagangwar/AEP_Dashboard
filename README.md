# AEP Dashboard

A web app that helps QA teams manage, run, and track their automated tests — all from one place, plus an AI-assisted testing suite for goal-based browser testing and requirements-to-checkpoint extraction.

Instead of digging through CI logs or asking "did anyone run the regression suite today?", your team gets a shared dashboard with live stats, test history, defect tracking, and the ability to kick off test runs with a button click. On top of that, an AI testing layer ("Vibe Testing") can run goal-based browser tests from a plain-English prompt, extract QA checkpoints from a spec document or a walkthrough video, and turn meeting notes into a Statement of Work.

---

## What's Inside

This repo has two main parts:

```
.
├── dashboard/          # The web app (frontend + backend + docker setup)
└── automation/         # Robot Framework test suites (the actual tests that get run)
```

**Dashboard** is a Next.js frontend talking to a FastAPI backend, backed by PostgreSQL and Redis. It handles user login, project management, test execution, results, reports, defect tracking, and the AI testing / SOW authoring suite.

**Automation** holds the Robot Framework + Playwright test suites. The dashboard discovers and triggers these tests and displays their results.

---

## Features

- **Dashboard home** — pass rates, recent runs, open defects, active projects, all at a glance
- **Projects** — group your test suites by project, with per-project environments and credential profiles
- **Test suites & runs** — organize tests, trigger runs, watch status (queued/running/passed/failed)
- **Results & reports** — drill into individual test results, see errors, screenshots, and execution times
- **Defect tracking** — log bugs from failed tests, assign severity, track them to closure
- **Vibe Testing (AI testing)** — goal-based AI browser agent, spec-document / walkthrough-video → QA checkpoint extraction, and reusable "Skills" recorded from passing runs
- **SOW authoring** — turn meeting transcripts, recordings, and design references into a structured Statement of Work, with section-level rewrite and export to Markdown/Word/PDF
- **User management** — roles with granular, per-feature permissions and JWT auth
- **Audit logs** — who did what and when
- **Auto-refresh / live updates** — dashboard stats and running tests update automatically

---

## Tech Stack

| Layer | What we use |
|-------|-------------|
| Frontend | Next.js (App Router), React, TypeScript/JavaScript, Tailwind CSS, shadcn-style UI components, TanStack Query |
| Backend | FastAPI, SQLAlchemy, Alembic (migrations), Pydantic |
| Database | PostgreSQL (managed/external — e.g. Neon; not bundled in Docker Compose) |
| Cache & Queue | Redis, Celery (async test execution and AI job processing) |
| Proxy | Nginx |
| Test Framework | Robot Framework + Playwright (Browser library) |
| AI / browser agent | Playwright-driven goal-based browser agent, with pluggable LLM providers (Google Gemini, OpenAI, Anthropic, OpenRouter — bring your own API key) |
| CI/CD | GitHub Actions (automation suite runs daily + on demand) |
| Containers | Docker Compose (backend, frontend, worker, Redis, Nginx in one command) |

---

## Getting Started

### What you need

- Docker and Docker Compose
- A PostgreSQL 15+ database (local install or a free managed instance, e.g. Neon)
- Python 3.11+ (for backend, if running locally)
- Node.js 18+ (for frontend, if running locally)
- Git

### Quickest way — Docker Compose

This brings up the app, Redis, the Celery worker, and Nginx (you provide the Postgres connection string).

```bash
# 1. Clone the repo
git clone <repo-url>
cd AEP_Dashboard

# 2. Set up your environment file
cp dashboard/backend/.env.example dashboard/.env
# Open dashboard/.env, uncomment the values you need, and fill them in
# (see Environment Variables below)

# 3. Start everything
cd dashboard
docker-compose up --build
```

Give it a minute, then open:

- **App**: http://localhost:3000
- **API docs (Swagger)**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/health

### For active development

If you're writing code and want hot reload, run Redis in Docker but the app locally (point `DATABASE_URL` at your own Postgres instance):

```bash
# Start just Redis
cd dashboard
docker-compose up redis

# In a new terminal — start the backend
cd dashboard/backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# In a new terminal — start the frontend
cd dashboard/frontend
npm install
npm run dev
```

Backend at http://localhost:8000, frontend at http://localhost:3000.

---

## Environment Variables

Copy `dashboard/backend/.env.example` as a starting template and fill these in (in `dashboard/.env` for Docker Compose, or `dashboard/backend/.env` for running the backend locally):

| Variable | What it does | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:password@host/dbname` |
| `JWT_SECRET_KEY` | Signing key for auth tokens | Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `JWT_ALGORITHM` | Token signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | How long access tokens last | `15` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | How long refresh tokens last | `7` |
| `FIRST_ADMIN_EMAIL` | Seed admin account (created on first run only) | `admin@example.com` |
| `FIRST_ADMIN_PASSWORD` | Seed admin password | Pick something strong |
| `CELERY_BROKER_URL` | Redis URL for task queue | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis URL for task results | `redis://redis:6379/0` |
| `AUTOMATION_ROOT` | Path to the `automation/` folder (enables the Execute feature) | `/automation` (Docker) |
| `VISUAL_AUDIT_ENABLED` | Turns on the Vibe Testing / visual QA feature set | `true` |
| `SOW_ENABLED` | Turns on the SOW authoring feature set | `true` |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | At least one, to power the AI features | your own key |

Full, current, inline-commented list: `dashboard/backend/.env.example`.

---

## Project Structure

```
dashboard/
├── backend/                # FastAPI app
│   ├── app/
│   │   ├── main.py         # Entry point
│   │   ├── api/v1/         # API route modules
│   │   ├── core/           # config, security, permissions, dependencies
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schemas/        # Pydantic request/response schemas
│   │   ├── services/       # business logic (LLM routing, ingestion, etc.)
│   │   └── workers/        # Celery tasks for async test execution & AI jobs
│   ├── alembic/             # Database migrations
│   └── requirements.txt
├── frontend/                # Next.js app
│   └── src/
│       ├── app/             # Pages (dashboard, projects, reports, defects, ai-testing, sow, etc.)
│       ├── components/      # Shared UI components
│       └── utils/, lib/     # API client, auth helpers
├── docker/                  # Dockerfiles and Nginx config
└── docker-compose.yml

automation/
├── ig_automation/           # Primary test suite
│   ├── tests/                # Test cases (login, dashboard, jobs, candidates)
│   ├── pages/                 # Page objects (locators)
│   ├── resources/              # Keywords and config
│   ├── libs/                    # Python helpers (auth bypass, AI locator healing)
│   └── requirements.txt
└── ig_automation_2/          # Secondary test suite
```

---

## Running the Automation Tests

The test suites use Robot Framework with Playwright.

```bash
cd automation/ig_automation

# One-time setup
pip install -r requirements.txt
rfbrowser init    # Downloads browser binaries

# Run all tests
robot --argumentfile local.args

# Run just the dashboard tests
robot --outputdir results --pythonpath . tests/dashboard/dashboard_tests.robot
```

Tests need a `.env` file with the target app's credentials — see the automation project's own docs for details.

**CI**: The automation suite runs daily via GitHub Actions, plus on-demand via manual trigger. Only tests tagged `ci-safe` run in CI (no CAPTCHA or file-dialog tests).

---

## API Endpoints

Once the backend is running, visit http://localhost:8000/docs for the full interactive API documentation.

The main routes:

| Route | What it does |
|-------|-------------|
| `POST /api/v1/auth/login` | Log in, get tokens |
| `GET /api/v1/auth/me` | Get current user info |
| `GET/POST /api/v1/projects/` | List or create projects |
| `GET/POST /api/v1/test-suites/` | List or create test suites |
| `GET/POST /api/v1/runs/` | List runs or trigger a new one |
| `GET /api/v1/test-results/` | Query test results |
| `GET/POST /api/v1/defects/` | List or log defects |
| `GET /api/v1/dashboard/stats` | All dashboard metrics in one call |
| `GET/POST /api/v1/ai-testing/` | Goal-based AI test runs and saved skills |
| `GET/POST /api/v1/sow/` | SOW document CRUD, generation, and export |
| `GET /api/v1/audit/` | Audit log |
| `GET /health` | Health check |

---

## Database Migrations

When you change a model, create and run a migration:

```bash
cd dashboard/backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

If migrations fail, make sure the `pgcrypto` extension exists:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

---

## Troubleshooting

| Problem | What to do |
|---------|-----------|
| `Address already in use` on port 8000 | Something else is using that port. Kill it or run on a different port: `uvicorn app.main:app --reload --port 8001` |
| Frontend can't reach backend | Check that `FASTAPI_URL` is set correctly in your `.env` or `frontend/.env.local` |
| Redis connection error | Make sure Redis is running: `docker ps \| grep redis` or `redis-cli ping` (should return PONG) |
| Alembic migration fails | Run `CREATE EXTENSION IF NOT EXISTS pgcrypto;` in your database first |
| Docker Compose won't start | Check your `.env` file exists at `dashboard/.env` and has valid values |
