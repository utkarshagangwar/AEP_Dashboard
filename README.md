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
