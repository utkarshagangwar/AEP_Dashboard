# AEP Dashboard

A web app that helps QA teams manage, run, and track their automated tests — all from one place.

Instead of digging through CI logs or asking "did anyone run the regression suite today?", your team gets a shared dashboard with live stats, test history, defect tracking, and the ability to kick off test runs with a button click.

---

## What's Inside

This repo has two main parts:

```
.
├── dashboard/          # The web app (frontend + backend + docker setup)
└── automation/         # Robot Framework test suites (the actual tests that get run)
```

**Dashboard** is a Next.js frontend talking to a FastAPI backend, backed by PostgreSQL and Redis. It handles user login, project management, test execution, results, reports, and defect tracking.

**Automation** holds the Robot Framework + Playwright test suites that test the InterviewGod application. The dashboard can trigger these tests and display their results.

---

## Features

- **Dashboard home** — pass rates, recent runs, open defects, active projects, all at a glance
- **Projects** — group your test suites by project
- **Test suites & runs** — organize tests, trigger runs, watch status (queued/running/passed/failed)
- **Results & reports** — drill into individual test results, see errors, screenshots, and execution times
- **Defect tracking** — log bugs from failed tests, assign severity, track them to closure
- **User management** — roles (Admin, QA Lead, QA Engineer, Viewer) with JWT auth
- **Audit logs** — who did what and when
- **Auto-refresh** — dashboard stats update every 10 seconds

---

## Tech Stack

| Layer | What we use |
|-------|-------------|
| Frontend | Next.js 16, React 18, Tailwind CSS, shadcn/ui, TanStack Query, Recharts |
| Backend | FastAPI, SQLAlchemy, Alembic (migrations), Pydantic |
| Database | PostgreSQL 15 |
| Cache & Queue | Redis 7, Celery (for async test execution) |
| Proxy | Nginx |
| Test Framework | Robot Framework 7.4 + Playwright (Browser library) |
| CI/CD | GitHub Actions (automation suite runs daily + on demand) |
| Containers | Docker Compose (everything in one command) |

---

## Getting Started

### What you need

- Docker and Docker Compose
- Python 3.11+ (for backend, if running locally)
- Node.js 18+ (for frontend, if running locally)
- Git

### Quickest way — Docker Compose

This brings up everything: database, Redis, backend, frontend, Nginx.

```bash
# 1. Clone the repo
git clone <repo-url>
cd AEP_Dashboard

# 2. Set up your environment file
cp dashboard/.env.example dashboard/.env
# Open dashboard/.env and fill in your values (see Environment Variables below)

# 3. Start everything
cd dashboard
docker-compose up --build
```

Give it a minute, then open:

- **App**: http://localhost:3000
- **API docs (Swagger)**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/health

### For active development

If you're writing code and want hot reload, run the databases in Docker but the app locally:

```bash
# Start just Redis
cd dashboard
docker-compose up redis

# In a new terminal — start PostgreSQL
docker run -d --name aep_postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=aepdb \
  -p 5432:5432 \
  postgres:15-alpine

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

Copy `dashboard/.env.example` to `dashboard/.env` and fill these in:

| Variable | What it does | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/aepdb` |
| `JWT_SECRET_KEY` | Signing key for auth tokens | Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `JWT_ALGORITHM` | Token signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | How long access tokens last | `15` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | How long refresh tokens last | `7` |
| `FIRST_ADMIN_EMAIL` | Seed admin account (created on first run only) | `admin@aep.local` |
| `FIRST_ADMIN_PASSWORD` | Seed admin password | Pick something strong |
| `CELERY_BROKER_URL` | Redis URL for task queue | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis URL for task results | `redis://redis:6379/0` |
| `FASTAPI_URL` | Backend URL (used by frontend) | `http://backend:8000` (Docker) or `http://localhost:8000` (local) |

---

## Project Structure

```
dashboard/
├── backend/                # FastAPI app
│   ├── app/
│   │   ├── main.py         # Entry point
│   │   ├── models/         # SQLAlchemy models (User, Project, TestRun, Defect, etc.)
│   │   ├── routers/        # API routes
│   │   └── workers/        # Celery tasks for async test execution
│   ├── alembic/            # Database migrations
│   └── requirements.txt
├── frontend/               # Next.js app
│   └── src/
│       ├── app/            # Pages (dashboard, projects, reports, defects, etc.)
│       ├── components/     # Shared UI components
│       └── utils/          # API client, auth helpers
├── docker/                 # Dockerfiles and Nginx config
├── docker-compose.yml
├── .env.example
└── LOCAL_SETUP.md          # Detailed setup guide with troubleshooting

automation/
├── ig_automation/          # Primary test suite
│   ├── tests/              # Test cases (login, dashboard, jobs, candidates)
│   ├── pages/              # Page objects (locators)
│   ├── resources/          # Keywords and config
│   ├── libs/               # Python helpers (auth bypass, AI locator healing)
│   └── requirements.txt
└── ig_automation_2/        # Secondary test suite
```

---

## Running the Automation Tests

The test suites use Robot Framework with Playwright. They test the InterviewGod app, not the dashboard itself.

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

Tests need a `.env` file with the target app's credentials — see the automation CLAUDE.md for details.

**CI**: The automation suite runs daily at 3:30 AM UTC via GitHub Actions, plus on-demand via manual trigger. Only tests tagged `ci-safe` run in CI (no CAPTCHA or file dialog tests).

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

For more details, see `dashboard/LOCAL_SETUP.md`.
