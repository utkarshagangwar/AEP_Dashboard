# AEP Dashboard — Local Development Setup & Run Guide

This guide walks you through running the Automation Execution Platform locally with the backend, database, and frontend.

---

## Prerequisites

- **Docker** + **Docker Compose** (recommended for PostgreSQL + Redis)
- **Python 3.11+** (for backend)
- **Node.js 18+** (for frontend)
- **PostgreSQL 15** (either via Docker or local installation)

---

## Option 1: Full Docker Compose (Recommended)

This is the fastest way to get everything running. The compose file brings up PostgreSQL, Redis, Backend, Celery Worker, Frontend, and Nginx all at once.

### Setup

```bash
# 1. Navigate to project root
cd C:\Users\utkar\Documents\B_T\automation_dashboard\dashboard

# 2. Ensure .env file exists at root with database and secrets
# Already in place at: .env
# Review it for correctness — especially DATABASE_URL and JWT_SECRET_KEY

# 3. Start all services
docker-compose up --build

# Expected output (5-10 seconds to full readiness):
# ✓ Redis healthy
# ✓ Backend running on :8000
# ✓ Frontend running on :3000
# ✓ Nginx running on :80 & :443
# ✓ Celery worker listening
```

### Access

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs (Swagger UI)
- **Health check**: http://localhost:8000/health

### Stop

```bash
docker-compose down
```

---

## Option 2: Local Development (Recommended for Active Development)

Run the backend and frontend locally while PostgreSQL + Redis run in Docker.

### Step 1: Start PostgreSQL & Redis in Docker

```bash
docker-compose up redis
# In another terminal:
docker run -d \
  --name aep_postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=aepdb \
  -p 5432:5432 \
  postgres:15-alpine

# Wait 5 seconds for PostgreSQL to be ready
```

### Step 2: Setup Backend

```bash
cd backend

# Activate venv (already created)
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install dependencies (if not already installed)
pip install -r requirements.txt

# Set up .env with local database URL
# Edit .env — change DATABASE_URL to local PostgreSQL:
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aepdb

# Run database migrations
alembic upgrade head

# Start backend
uvicorn app.main:app --reload --port 8000
```

✓ Backend running on http://localhost:8000

### Step 3: Start Frontend

```bash
cd frontend

# Install dependencies (if not already installed)
npm install

# Ensure .env.local points to backend:
# NEXT_PUBLIC_API_URL=http://localhost:8000
# FASTAPI_URL=http://localhost:8000

# Start dev server
npm run dev
```

✓ Frontend running on http://localhost:3000

### Step 4: (Optional) Start Celery Worker

```bash
cd backend
celery -A app.workers.celery_app:celery_app worker --loglevel=info
```

---

## Option 3: Database via Cloud (Neon)

If you prefer not to run PostgreSQL locally, the project is already configured to use **Neon** (cloud PostgreSQL):

```bash
# .env already contains:
DATABASE_URL=postgresql://neondb_owner:npg_...@ep-wild-silence-adry3yuy-pooler.c-2.us-east-1.aws.neon.tech/neondb

# Just ensure Docker Redis is running:
docker run -d -p 6379:6379 --name aep_redis redis:7-alpine

# Then run backend locally:
cd backend
.venv\Scripts\activate
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# And frontend:
cd frontend
npm run dev
```

⚠️ **Note**: Neon database may go idle on free tier; re-activate if needed.

---

## Verify Everything is Working

### 1. Backend Health Check

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","version":"1.0.0"}
```

### 2. Login (Get Access Token)

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "admin@aep.local",
    "password": "REDACTED"
  }' | jq .
```

**Expected response**:
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 900
}
```

### 3. Get Current User (Authenticated Request)

```bash
# Replace <access_token> with token from login
curl -s http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <access_token>" | jq .
```

### 4. Check Frontend

Open http://localhost:3000 in browser. If you see the login page, frontend is working.

### 5. Swagger UI

Visit http://localhost:8000/docs to explore all API endpoints interactively.

---

## Troubleshooting

### Backend won't start: "Address already in use"

```bash
# Port 8000 is in use. Either:
# 1. Kill the process on port 8000
lsof -ti:8000 | xargs kill -9      # macOS/Linux
netstat -ano | findstr :8000       # Windows

# 2. Or run backend on a different port:
uvicorn app.main:app --reload --port 8001
```

### PostgreSQL connection error

```bash
# Verify the DATABASE_URL format:
# postgresql://user:password@host:5432/dbname?sslmode=require

# Test with psql:
psql postgresql://postgres:postgres@localhost:5432/aepdb
```

### Frontend can't reach backend

```bash
# Ensure NEXT_PUBLIC_API_URL or FASTAPI_URL is set:
# In frontend/.env.local:
NEXT_PUBLIC_API_URL=http://localhost:8000
FASTAPI_URL=http://localhost:8000

# Then restart frontend:
npm run dev
```

### Redis connection error

```bash
# Verify Redis is running:
redis-cli ping
# Expected: PONG

# Or check Docker:
docker ps | grep redis
```

### Alembic migration fails

```bash
# Ensure pgcrypto extension is created:
psql -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" aepdb

# Then retry migration:
alembic upgrade head
```

---

## Quick Reference: Common Commands

| Task | Command |
|------|---------|
| Full Docker stack | `docker-compose up --build` |
| Backend only (Docker) | `docker-compose up backend` |
| Stop all | `docker-compose down` |
| Backend venv activate | `.venv\Scripts\activate` (Windows) |
| Backend run | `uvicorn app.main:app --reload --port 8000` |
| Frontend run | `npm run dev` |
| DB migrations | `alembic upgrade head` |
| Login | `curl -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@aep.local","password":"REDACTED"}'` |
| Swagger UI | http://localhost:8000/docs |

---

## Next Steps

Once everything is running:

1. **Test Authentication**: Login, verify token generation, test protected endpoints.
2. **Test Database**: Check users table, verify seed data.
3. **Test API**: Explore Swagger UI at http://localhost:8000/docs.
4. **Test Frontend**: Navigate frontend at http://localhost:3000, verify it connects to backend.
5. **Test Celery** (if needed): Trigger a task, monitor worker logs.

---

## Environment Variables Reference

See `.env` for current values. Key variables:

- `DATABASE_URL` — PostgreSQL connection string
- `JWT_SECRET_KEY` — Secret for signing JWTs
- `FIRST_ADMIN_EMAIL` — Seed admin email (only used on first startup)
- `FIRST_ADMIN_PASSWORD` — Seed admin password
- `CELERY_BROKER_URL` — Redis URL for Celery tasks
- `NEXT_PUBLIC_API_URL` — Frontend's backend API URL

---

## Docker Images Used

- `postgres:15-alpine` — Database
- `redis:7-alpine` — Cache & queue
- `python:3.11-slim` — Backend runtime
- `node:18-alpine` — Frontend builder
- `nginx:alpine` — Reverse proxy

---

## Performance Tips

- **Docker Compose**: Fastest for full stack, no setup overhead.
- **Local Dev**: Better for rapid iteration on backend/frontend code.
- **Cloud DB + Local**: Balances simplicity (no local PostgreSQL) with speed.

Choose based on your workflow. Docker Compose is recommended for first-time verification.
