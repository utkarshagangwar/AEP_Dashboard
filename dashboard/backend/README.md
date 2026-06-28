# Automation Execution Platform — Backend (Phase 2: Auth & RBAC)

FastAPI backend implementing JWT authentication and role-based access control.

## Folder structure

```
backend/
  app/
    api/v1/        # route files (auth.py, users.py, router.py)
    core/          # config, logging, security, database, dependencies, seed
    models/        # SQLAlchemy ORM models
    schemas/       # Pydantic schemas
    services/      # business logic
    main.py        # FastAPI entrypoint
  alembic/         # migrations
  alembic.ini
  requirements.txt
  .env.example
```

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then edit secrets
```

Set a real `DATABASE_URL`, `JWT_SECRET_KEY`, `FIRST_ADMIN_EMAIL`, and
`FIRST_ADMIN_PASSWORD` in `.env`.

## Database migrations

```bash
alembic upgrade head
```

> Requires the `pgcrypto` extension for `gen_random_uuid()`:
> `CREATE EXTENSION IF NOT EXISTS pgcrypto;`

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

On first startup, if no users exist, an Admin is seeded from
`FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`.

## Verify

```bash
# Login
curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"<pw>"}'

# Me (use access_token from login)
curl -s localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access_token>"

# Logout (revokes refresh token)
curl -s -X POST localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer <access_token>" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh_token>"}'
```
