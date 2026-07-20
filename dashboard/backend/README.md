# AEP Dashboard — Backend

FastAPI service powering the AEP ("Automation Execution Platform") dashboard: auth/RBAC,
project & test-suite management, **real** Robot Framework execution, defect tracking, and
the "Vibe Testing" AI suite (goal-based browser agent testing, SOW/video-to-checkpoint
extraction, visual regression auditing, an autonomous orchestrator, and reusable "skills").

> Keep this file in sync with the code. It documents current behavior, not aspirational
> behavior — where something is half-wired or inconsistent, it says so explicitly (see
> [Known issues](#known-issues-found-2026-07-15)) rather than describing the intended end
> state as if it were real.

## Tech stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.115.6, Uvicorn 0.34 |
| ORM / migrations | SQLAlchemy 2.0.36, Alembic 1.14 (25 migrations) |
| Database | PostgreSQL (external — Neon in the reference `.env.example`, not containerized) |
| Background jobs | Celery 5.4 + Redis 7 broker/backend; Celery Beat runs **embedded** in the worker process (`-B` flag, no separate beat container) |
| Auth | JWT access tokens (`python-jose`) + opaque, hashed, single-use refresh tokens; `passlib[bcrypt]` for password hashing |
| Rate limiting | `slowapi` — 100 req/min per IP globally, 10 req/min on the three `/auth` write endpoints |
| Browser automation ("Hands") | `browser-use` 0.1.45 + Playwright 1.49, driven over CDP on `--remote-debugging-port=9222` |
| LLM routing ("The Router") | `litellm` 1.74 for Visual QA/orchestrator calls; LangChain (`langchain-google-genai`, `langchain-openai`, `langchain-anthropic`) for the goal-based agent's own model calls |
| AI providers | Google Gemini, OpenAI, Anthropic, OpenRouter — selected per task, with key rotation and provider fallback |
| Visual diffing | `pixelmatch` 0.3.0 + Pillow, plus an AI vision pass for structural differences |
| Video/PDF ingestion | Gemini Files API (direct REST) for video, `pypdf` for SOW documents, `ffmpeg`/`ffprobe` for still-frame extraction (best-effort) |
| Robot Framework execution | Spawns real `robot`/`pabot` subprocesses against `AUTOMATION_ROOT`, with a custom RF listener writing results straight to Postgres |

## Architecture

```
                      ┌────────────┐
   browser ─────────▶ │   nginx    │  (TLS, :80/:443)
                      └─────┬──────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       ┌─────────────┐            ┌──────────────┐
       │  frontend   │  /api/*    │   backend    │
       │  (Next.js)  │───────────▶│  (FastAPI)   │  :8000
       └─────────────┘  proxy     └──────┬───────┘
                                          │ enqueues jobs
                                          ▼
                                   ┌──────────────┐        ┌─────────┐
                                   │ celery_worker│◀──────▶│  redis  │
                                   │ (+ embedded  │ broker/ └─────────┘
                                   │    beat)     │ backend
                                   └──────┬───────┘
                     ┌────────────────────┼──────────────────────┐
                     ▼                    ▼                      ▼
              PostgreSQL (external)  visual_qa_data +      AI providers /
              (all app state)        automation/ (shared    Robot Framework
                                      volumes: uploads,      subprocess
                                      screenshots, .robot
                                      test projects)
```

The frontend never talks to Celery, Postgres, or `robot` directly — every mutation goes
through this API, which writes to Postgres and, for long-running work, enqueues a Celery
task and returns immediately. The `backend` and `celery_worker` containers share two Docker
volumes: a named volume `visual_qa_data` (uploaded SOWs/videos/screenshots/diffs) and a
**bind mount of the sibling `../automation` folder** — that bind mount is the literal
mechanism by which this service finds and executes real `.robot` test suites (see
[Robot Framework execution](#robot-framework-execution--this-is-real-not-mocked)).

## Folder structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, lifespan/seed, CORS, rate limiting, router mount
│   ├── api/v1/               # one route module per resource (15 files, ~65 routes total)
│   ├── core/                 # config.py (typed Settings), security.py (JWT), permissions.py,
│   │                         # dependencies.py (auth/RBAC deps), rate_limit.py, seed.py, db.py
│   ├── models/                # SQLAlchemy ORM models (12 files)
│   ├── schemas/                # Pydantic request/response schemas (mirror models 1:1)
│   ├── services/                # business logic — see below
│   └── workers/
│       ├── celery_app.py       # broker/backend config, beat schedule, task module registry
│       └── tasks/               # one module per Celery task family (9 files)
├── alembic/versions/            # 25 migrations, 0001..0025
├── requirements.txt              # core deps
├── requirements-robot.txt        # Robot Framework + Browser library deps (installed separately)
└── .env.example
```

## App wiring (`app/main.py`)

- `FastAPI(title="Automation Execution Platform API", version="0.2.0", lifespan=lifespan)`.
- **Lifespan**: on startup, configures logging and calls `seed_initial_admin()` (creates the
  `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD` account if no users exist yet — idempotent, safe
  to run every boot). Migrations are **not** run here — they run separately in the Docker
  entrypoint (`alembic upgrade head && uvicorn ...`), on every container start.
- **CORS**: only registered `if CORS_ALLOWED_ORIGINS` is non-empty (comma-separated origins).
  Default is empty → no `CORSMiddleware` at all, because today's deployment always goes
  browser → Next.js proxy → FastAPI (same-origin from the browser's perspective). The env var
  exists for a documented future mode where the browser calls FastAPI directly.
- **Rate limiting**: `slowapi`, 100/min per IP globally via `SlowAPIMiddleware`; `login`,
  `refresh`, `logout` are individually capped at 10/min.
- **Routing**: a single `app.include_router(api_router)` mounts every `/api/v1/*` route.
- **Health check**: `GET /health` (no `/api/v1` prefix) → `{"status": "ok", "version": "1.0.0"}`
  — note this hardcoded `"1.0.0"` doesn't match the app's own `version="0.2.0"` (cosmetic
  inconsistency, not a bug).

## Auth & RBAC

**JWT flow:**
1. `POST /api/v1/auth/login` verifies email/password (bcrypt) and `is_active`, then issues a
   short-lived **access token** (JWT, HS256, `ACCESS_TOKEN_EXPIRE_MINUTES` — default 15) and
   an opaque 64-hex **refresh token** (`REFRESH_TOKEN_EXPIRE_DAYS` — `config.py`'s own default
   is 7, but the `.env.example`/`.env.sample` templates set it to `1` as of 2026-07-15 for a
   24-hour session; the frontend's matching env var must stay in sync — see
   [dashboard/frontend/README.md](../frontend/README.md#auth)).
2. Only the **SHA-256 hash** of the refresh token is persisted in `refresh_tokens.token` —
   the raw value is never stored, only ever returned once to the client.
3. `POST /api/v1/auth/refresh` looks up the token by hash, checks `is_revoked`/`expires_at`,
   then **rotates** it (revokes the old row, issues a new pair) — refresh tokens are
   single-use.
4. `POST /api/v1/auth/logout` revokes one refresh token. There's no "revoke all sessions" or
   access-token blocklist — a stolen access token stays valid until its own (short) expiry
   regardless of logout. That's a standard short-lived-JWT tradeoff, not an oversight.
5. Every protected route depends on `get_current_user`, which validates the bearer token
   (signature, expiry, `type == "access"`) and loads + `is_active`-checks the `User` row.

**RBAC is hybrid — permission-based for features, role-based for admin operations:**
- `UserRole` (`admin, qa_lead, qa_engineer, developer, viewer, sales, ba, hr`) is explicitly
  documented in the model as **descriptive only** — `admin` always has full access; every
  other role has zero *implicit* permissions.
- Real feature access comes from `User.permissions`, a JSONB list of keys grantable per-user
  from the admin-only Users page. `app/core/permissions.py` defines the grantable set:
  `projects, test_suites, test_runs, execute, defects, reports, vibe_testing`.
- `require_permission(key)` (admins bypass, everyone else needs `key` in their list) gates
  feature routes; `require_roles(...)` (coarser, always admin-only) gates user management,
  audit-log viewing, and hard-deletes.
- Of the 7 grantable permission keys, only **`projects`, `test_suites`, `execute`, `defects`,
  `vibe_testing`** are ever actually checked by a route — `test_runs` and `reports` are
  grantable in the UI but enforced nowhere (see [Known issues](#known-issues-found-2026-07-15)).

## API surface (`app/api/v1/`) — ~65 routes across 15 modules

| Module | Prefix | What it covers |
|---|---|---|
| `auth.py` | `/auth` | login, refresh, logout, `me` |
| `audit.py` | `/audit` | paginated audit log — admin only |
| `dashboard.py` | `/dashboard` | `GET /stats` — every dashboard KPI in one call, optional `project_id` scope |
| `users.py` | `/users` | user CRUD, role/permission assignment (admin only), `assignable` lookup for pickers |
| `projects.py` | `/projects` | project CRUD, `discover-suites` (scans `AUTOMATION_ROOT`, auto-registers projects/suites) |
| `test_suites.py` | `/projects/{id}/suites` | suite CRUD scoped to a project |
| `test_suites_list.py` | `/test-suites` | flat cross-project suite listing (raw SQL) |
| `test_results.py` | `/test-results` | individual test-case outcomes, filterable by run/status |
| `executions.py` | `/runs` | trigger/list/cancel/delete a run, reconcile a stuck run, `GET /{id}/stream` (SSE live status) |
| `reports.py` | `/reports` | run history/detail/export, video playback, AI-suggestion review + approval |
| `defects.py` | `/defects` | defect CRUD; **developers only see defects assigned to them** |
| `ai_runs.py` | `/ai-testing` | credential profiles, goal-based AI runs, saved "skills", `GET /runs/{id}/stream` (SSE) |
| `visual_audit.py` | `/visual-audits` | references, Figma import, SOW/video ingestion, pixel-diff+AI audit runs — **gated by `VISUAL_AUDIT_ENABLED`, 404s entirely when off** |
| `orchestrator.py` | `/orchestrator` | "The Brain" — submit a goal/URL/design-reference combo, let it route to Hands/Judge/self-execute — also gated by `VISUAL_AUDIT_ENABLED` |

Every list endpoint returns the same envelope shape: `{data, total, page, limit}`. Full
interactive docs at `/docs` once the server is running.

## Data model (`app/models/`)

**Auth/RBAC**
- `users` — `role` (descriptive enum), `permissions` (JSONB list, the real access-control source)
- `refresh_tokens` — hashed token, `is_revoked`, `expires_at`
- `audit_logs` — actor, action, resource, JSONB `details`, IP

**Core QA domain**
- `projects` — `name` (unique among active rows) vs. `folder_name` (immutable key used to
  match `automation/` folders — separate from the user-editable display name), `environments`
  (`ARRAY(String)`), `product` enum (defined, **not yet exposed by any route** — see
  [Known issues](#known-issues-found-2026-07-15))
- `test_suites` — belongs to a project, `suite_type` (`smoke, regression, sanity,
  exploratory, full`)
- `test_runs` — `celery_task_id`, `status` (`queued, pending, running, passed, failed,
  cancelled, error`), timing
- `test_results` — one row per test case per run, `status`, `duration_ms`, `error_message`,
  `stack_trace`, `tags`
- `defects` — linked to a `test_result`, `severity`/`status` enums, `assigned_to`

**Visual QA / "Vibe Testing" — the Memory Bank pattern**
- `design_artifacts` — one row per uploaded source (Figma PNG / SOW / video), **deduplicated
  by SHA-256** so identical content is never re-analyzed or re-billed; `platform_name`
  (mandatory for video, since 2026-07-12 — see history below); `parse_status`
- `sow_parts` — chunked SOW text (large docs split ~20k chars/part), analyzed on demand,
  merged into `design_rules`
- `design_rules` — final merged JSONB checkpoints per artifact
- `visual_runs` / `visual_findings` — one pixel-diff+vision audit run and its findings
  (severity, region bbox, engine: `pixel_diff` or `vision`)

**AI test runs & skills**
- `ai_credential_profiles` — Fernet-encrypted login credentials scoped by allowed domain
- `ai_test_runs` / `ai_run_events` — one goal-based agent run and its step-by-step timeline
- `ai_skills` — reusable prompt or recorded-replay skills, upserted by `goal_hash` (goal-based)
  or `(artifact_id, slugified title)` (SOW/video-extracted); `manually_edited=True` protects
  hand edits from being overwritten by re-analysis

**Orchestrator ("The Brain")**
- `orchestrator_runs` — a routed run that may delegate to a real `AITestRun` and/or
  `VisualRun` under the hood (denormalized FKs link back to whichever sub-agent actually ran)
- `orchestrator_step_decisions` — audit trail of which step (Hands/Judge/self-execute) was
  invoked or skipped and why

## Services (`app/services/`) — business logic layer

| Service | Responsibility |
|---|---|
| `auth_service.py` | login, token issue/rotate/revoke |
| `user_service.py` | user CRUD |
| `audit_service.py` | best-effort audit-log writer (never raises, never blocks a mutation) |
| `dashboard_service.py` | computes every dashboard KPI/chart via raw-SQL aggregates |
| `credential_service.py` | Fernet encryption for AI credential profiles (`AI_CREDENTIAL_KEY` — see [Known issues](#known-issues-found-2026-07-15)) |
| `figma_service.py` | Figma REST client (stdlib `urllib`, deliberately no `requests`/`httpx` — avoids a dependency conflict with `browser-use`'s pinned tree) |
| `suite_discovery.py` | scans `AUTOMATION_ROOT` for `<project>/tests/<suite>/<suite>_tests.robot`, auto-registers `Project`/`TestSuite` rows |
| `llm_router.py` | "The Router" — `litellm`-based primary→fallback model chain with retries and strict-JSON output mode; used by the Judge, SOW/video ingestion, and orchestrator |
| `model_pool.py` | live-probes which LLM providers/keys actually work right now, resolves an abstract model choice into a concrete client for the orchestrator |
| `ai_runner.py` | "The Hands" — launches headless Chromium over CDP, runs `browser-use`'s `Agent` for goal-based execution or `rerun_history()` for skill replay, provider-precedence LLM selection (Anthropic → OpenAI → Google, each probed before use), Google-key rotation on 429, post-run narrative summary |
| `visual_judge.py` | "The Judge" — deterministic pixel-diff (`pixelmatch`, clustered into bounding boxes) plus an AI vision pass for structural differences only; skips the vision call entirely when the pixel-diff verdict is already conclusive |
| `orchestrator.py` | "The Brain" — deterministic rules-first routing (goal-only → self-execute; artifact+URL → Judge; URL+goal → Hands; all three → one cheap classifier call) with a full decision audit trail |
| `design_ingest.py` | SOW text extraction (.txt/.md/.pdf), chunking, per-chunk LLM checkpoint extraction, deterministic skill-markdown rendering |
| `video_ingest.py` | Gemini Files API video digestion, still-frame extraction assist, hard-gates on `platform_match` (see [Feature history](#feature-history) below) |
| `skill_store.py` | shared upsert logic for both goal-based and prompt-only skills |

## Background jobs (`app/workers/`)

Celery broker/backend default to `redis://localhost:6379/0` (`redis://redis:6379/0` in
Docker). `task_acks_late=True`, `worker_prefetch_multiplier=1`,
`task_soft_time_limit=1800`/`task_time_limit=3600` (video ingestion overrides to a 1200s
soft limit). **Beat runs embedded in the worker** (`-B` flag) — there is intentionally no
separate beat container, which means scaling `celery_worker` to more than one replica would
silently duplicate the periodic tasks below.

| Task | Triggered by | What it does |
|---|---|---|
| `execute_test_suite` | `POST /runs` | Finds the matching `.robot` file under `AUTOMATION_ROOT`, spawns `robot` (or `pabot` if `PABOT_PROCESSES>1`) as a real subprocess with a custom `--listener`, live-parses stdout for PASS/FAIL, falls back to parsing `output.xml` |
| `reconcile_stale_runs` *(periodic, 5 min)* | Celery beat | Recovers runs stuck `running`/`queued` >10 min by re-parsing `output.xml` |
| `LiveResultListener` | invoked by `robot`/`pabot` itself | RF listener v3 class — inserts each `TestResult` row into Postgres immediately after `end_test`, via `AEP_DATABASE_URL` |
| `run_ai_test_task` | `POST /ai-testing/runs` | Runs a goal-based AI test via `ai_runner`, persists live events, generates a narrative summary, auto-saves a skill on pass |
| `replay_skill_task` | `POST /ai-testing/skills/{id}/replay` | Deterministic replay of a saved skill's recorded history, with AI fallback if replay fails |
| `run_visual_audit_task` | `POST /visual-audits` | Screenshots the live page (own Chromium instance — no CDP port conflict with `ai_runner`), runs the Judge, persists findings |
| `ingest_sow_task` / `analyze_sow_part_task` | `POST /visual-audits/sow` (auto for single-part), `POST /.../parts/{n}/analyze` (manual, multi-part) | Extracts/chunks/analyzes a SOW, merges checkpoints, auto-saves functional checkpoints as skills |
| `import_figma_frames_task` | `POST /visual-audits/figma/import` | Batch-exports/downloads selected Figma frames |
| `ingest_video_task` | `POST /visual-audits/video` | Digests a walkthrough video, saves checkpoints + skills |
| `run_orchestrator_task` | `POST /orchestrator/runs` | Wraps `orchestrator.execute_run()` with a safety net that force-terminates the run on any unhandled exception |
| `reconcile_stale_visual_qa` *(periodic, 5 min)* | Celery beat | Marks `SowPart`/`DesignArtifact` rows stuck `processing` (staleness-timed) as `error`, so the UI's Retry button works |

## Robot Framework execution — this is real, not mocked

Confirmed by tracing the code end to end:

1. `suite_discovery.py` and `execution.py` both look for
   `<project>/tests/<suite>/<suite>_tests.robot` under `AUTOMATION_ROOT` (bind-mounted to
   `/automation` in Docker from the repo's sibling `automation/` folder — see
   [automation/ig_automation/README.md](../../automation/ig_automation/README.md)).
2. `execute_test_suite` genuinely spawns `robot`/`pabot` as a subprocess with real CLI flags
   (`--outputdir`, `--pythonpath`, `--listener rf_listener.py:<run_id>`,
   `--variable BROWSER:headlesschromium`), streams stdout live, and falls back to parsing the
   generated `output.xml` if live parsing found nothing.
3. `rf_listener.py` is a genuine Robot Framework Listener API v3 class that inserts a
   `TestResult` row into Postgres right after each test ends.
4. `reports.py` reads real files back out of the mounted automation folder — recorded videos
   from `test-artifacts/videos/`, AI locator-repair suggestions from
   `test-artifacts/ai_suggestions/`.
5. The stale-run reconciler (on-demand and periodic) exists specifically to recover from real
   subprocess/worker crashes — further evidence this is a live integration with real failure
   modes, not a mock.

This is the one part of the top-level [dashboard/README.md](../README.md)'s description that
is fully implemented today, not aspirational.

## Config & environment variables

Only ~14 vars are validated through the typed `Settings` class
(`app/core/config.py`) — everything else (all Visual QA/AI/Figma tuning) is read ad hoc via
`os.environ.get(...)` scattered across services/routes/tasks, with duplicated defaults in
places (see [Known issues](#known-issues-found-2026-07-15)). See `.env.example` for the full,
current list with inline explanations — it is kept more up to date than any table here would
stay. The load-bearing ones:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `JWT_SECRET_KEY` | yes | JWT signing secret |
| `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` | yes | seed admin, first boot only |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | no (defaults to `redis://localhost:6379/0`) | Celery/Redis |
| `AUTOMATION_ROOT` | yes, for Execute to work | path to the `automation/` folder |
| `PABOT_PROCESSES` | no (default `1`) | within-suite parallelism |
| `CORS_ALLOWED_ORIGINS` | no | only needed for direct browser→FastAPI calls |
| `VISUAL_AUDIT_ENABLED` | no (feature off if unset) | master switch for all Visual QA / orchestrator routes |
| `AI_CREDENTIAL_KEY` | no, but should be set in production | Fernet key for AI credential profiles — **falls back to an ephemeral in-memory key if unset**, meaning any profile created before a restart becomes permanently undecryptable after one (see [Known issues](#known-issues-found-2026-07-15)) |
| `GEMINI_API_KEY(S)` / `ANTHROPIC_API_KEY(S)` / `OPENAI_API_KEY(S)` / `OPENROUTER_API_KEY` | at least one, for AI features | LLM provider keys, plural variants accept a comma list for key rotation |
| `FIGMA_API_TOKEN` | only for Figma import | read-scope personal access token |

## Database migrations

25 migrations (`0001`..`0025`). The most recent 10 track the Vibe Testing feature build-out:
`add_ai_skills` → `add_user_permissions_and_roles` (the RBAC system) → status indexes →
project environments → `folder_name` (the suite-discovery identity fix) → SOW chunking →
skill provenance/manual-edit tracking → design-artifact staleness tracking → video
platform-name requirement. Visual QA/AI is by far the most actively evolving part of the
schema.

```bash
cd dashboard/backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head          # requires: CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

Migrations run automatically as part of the container start command
(`alembic upgrade head && uvicorn ...`), every boot — not as a separate one-shot job.

## Feature history

### 2026-07-12 — Video Walkthrough: mandatory platform name + anti-hallucination guard

The Video Walkthrough feature was extracting checkpoints that matched an uploaded SOW's
content instead of the actual video content. Root cause: not a linking bug — the video that
had been uploaded for testing was itself a recording of this same dashboard's SOW panel, so
Gemini was correctly reading what was on screen; it was simply the wrong file for the intent
behind the report. Fix: `design_artifacts.platform_name` is now a required field on every
video upload (422 if missing/blank), the Gemini prompt is anchored to the declared platform
with an explicit grounding rule, and a `platform_match`/`brand_evidence` check hard-fails
ingestion (`IngestError`, no skill ever saved) if the on-screen content doesn't match what was
declared — including a follow-up fix the same day after the mismatch check itself first
produced a false negative (native video understanding under-weighted a small persistent
sidebar logo; fixed by attaching plain still JPEG frames via `ffmpeg` alongside the video for
the model to check first).

### 2026-07-12 — Skills tab: sorting + bulk actions

`GET /ai-testing/skills` gained `sort_by`/`sort_dir`; two new bulk endpoints
(`bulk-delete`, `bulk-assign-project`, both permission-gated, each one transaction + one audit
log entry). See [dashboard/README.md](../README.md#changelog) for full changelog detail —
that file remains the canonical dated changelog; this README documents current architecture,
not a running history.

## Known issues (found 2026-07-15)

Found while writing this documentation pass — flagging for a human decision, nothing here has
been changed:

1. **`dashboard/.env.example` was committed to git with a real, non-placeholder Neon
   PostgreSQL connection string** (full host, username, password). **Partially fixed
   2026-07-15**: the file is now gitignored and untracked going forward (`git rm --cached`),
   and a clean, placeholder-only template now lives at `dashboard/.env.sample` (tracked) —
   onboarding docs were updated to `cp dashboard/.env.sample dashboard/.env`.
   **Still outstanding, and this is the actually urgent part**: that credential has been in
   this file since the very first commit (`5ad5626`) and **is already on GitHub** — the
   remote (`origin/main`) was confirmed to have this history. Removing the file from the
   working tree does not remove it from git history or from GitHub. Two things still need a
   human to do them (neither is possible from here): **rotate the Neon database credential**,
   and decide whether to scrub git history (`git filter-repo`/BFG + a force-push) given the
   credential is presumed already exposed either way.
2. ~~Two grantable permission keys were dead~~ — **fixed 2026-07-15**: `test_runs` and
   `reports` did nothing (no route ever checked them) and enforcing them now would have
   risked locking out users who currently rely on unrestricted Reports/Runs access, so they
   were removed from `PERMISSION_KEYS` and from the admin Users permissions UI instead of
   being wired up. The "Reports" nav link in `AppShell.jsx` no longer gates on a permission
   (it never matched actual backend access anyway — `reports.py` only ever required being
   logged in).
3. **`AI_CREDENTIAL_KEY` silently falls back to an ephemeral, process-local Fernet key** if
   unset — any AI credential profile created before a container restart becomes permanently
   undecryptable after one. **Partially addressed 2026-07-15**: the var is now documented
   with a generation command in both `.env.example`/`.env.sample`, and `main.py`'s startup
   lifespan now logs an explicit warning at boot if it's unset, so it's visible in server
   logs immediately rather than only the first time a profile is saved. The underlying
   behavior (ephemeral key, silent per-restart data loss) is unchanged — genuinely hardening
   this would mean either failing fast at startup if the Vibe Testing feature is enabled
   without this key, or persisting the key somewhere durable, and both are real behavior
   changes deserving their own decision rather than a docs-pass fix.
4. **Two parallel config systems**: the typed `Settings` class covers ~14 vars; 25+ more
   (all Visual QA/AI/Figma tuning) are read ad hoc via `os.environ.get(...)` with no central
   validation. Some defaults are duplicated inconsistently between call sites — e.g.
   `VISUAL_VIDEO_MAX_MB` defaults to `50` in `visual_audit.py` code but `100` in both
   `.env.example` files.
5. **`requirements.txt` lists `Pillow==12.2.0` twice** (consecutive lines) — harmless (pip
   dedupes) but worth a one-line cleanup.
6. **`langchain-anthropic` and the bare `anthropic`/`openai` SDKs are imported directly in
   `ai_runner.py`** (`from langchain_anthropic import ChatAnthropic`, `from anthropic import
   RateLimitError`, `from openai import RateLimitError`) but are not declared in
   `requirements.txt` — they currently arrive transitively via `browser-use`'s own pins. Works
   today, but a future `browser-use` bump could change these versions with nothing in this
   project's own requirements file to catch it.
7. **`/health`'s hardcoded `"version": "1.0.0"`** doesn't match `FastAPI(version="0.2.0")` in
   the same file — pick one source of truth.
8. **`app/models/project.py`'s `Product` enum** (`vikaas, vidya, atg_meeting_recorder, axon,
   revops, lms`) is a real nullable column, but `ProjectCreate`/`ProjectUpdate` schemas have
   no `product` field and no route ever sets or exposes it — looks like a partially-wired
   feature; worth confirming with the team whether it's planned or safe to remove.
9. **Very repetitive per-route error handling** (`try/except HTTPException: raise / except
   SQLAlchemyError: db.rollback(); raise HTTPException(500)`, hand-copied into dozens of
   endpoints) instead of a small number of centralized FastAPI exception handlers on `app`
   itself — only the rate-limit exception is handled that way today. Not a bug, but real
   maintainability debt.
10. **No separate Celery beat container** — Beat runs embedded in the single `celery_worker`
    replica (`-B` flag), explicitly assuming exactly one replica. Scaling `celery_worker`
    horizontally would silently duplicate both periodic reconciliation tasks. Worth a
    dedicated beat service before ever scaling workers.
