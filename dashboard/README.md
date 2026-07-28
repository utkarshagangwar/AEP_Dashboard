# AEP — Automation Execution Platform

Internal QA platform combining manual test-suite management (real Robot
Framework execution) with an AI-driven testing suite: goal-based browser
agents ("Vibe Testing"), requirements/video-to-checkpoint extraction, visual
regression auditing, an autonomous orchestrator, and a SOW (Statement of
Work) authoring/rewrite pipeline.

> **This is the single README for the whole project** (frontend + backend +
> infra). `backend/README.md` and `frontend/README.md` are now redirect
> stubs — do not re-create separate docs for those folders; add to this file
> instead. **Maintenance rule:** update this file after any change that
> affects functionality, architecture, or data flow, and add a dated entry to
> [Changelog](#changelog).

---

## 1. Tech stack

| Layer | Technology |
|---|---|
| Frontend framework | Next.js 16.2 (App Router), React 18.3 (not React 19) |
| Frontend language | TypeScript 5.9 — but most top-level pages are still plain `.jsx`; only `ai-testing` and newer components are `.tsx` |
| UI primitives | `@base-ui/react` 1.6 (not Radix, despite `components.json` calling the style `base-nova`, shadcn-CLI-managed) |
| Styling | Tailwind CSS v3.4 + `class-variance-authority` + `tailwind-merge`, oklch design tokens |
| Data fetching (frontend) | TanStack Query v5 — the real state-management layer for almost everything |
| Backend framework | FastAPI 0.115.6, Uvicorn 0.34 |
| ORM / migrations | SQLAlchemy 2.0.36, Alembic 1.14 (30 migrations) |
| Database | PostgreSQL (external/managed — Neon in the reference config; not containerized) |
| Background jobs | Celery 5.4 + Redis 7 broker/backend; Celery Beat runs **embedded** in the worker process (`-B` flag, no separate beat container) |
| Auth | JWT access tokens (`python-jose`) + opaque, hashed, single-use refresh tokens; `passlib[bcrypt]` for password hashing |
| Rate limiting | `slowapi` — 100 req/min per IP globally, 10 req/min on `/auth` write endpoints, 5 attempts/15 min on frontend login proxy |
| Browser automation ("The Hands") | Playwright 1.49 + `browser-use` 0.1.45 (goal-based AI agent), driven over CDP on `--remote-debugging-port=9222` |
| LLM routing ("The Router") | `litellm` 1.74 for Visual QA/orchestrator calls; LangChain (`langchain-google-genai`, `langchain-openai`, `langchain-anthropic`) for the goal-based agent's own calls |
| AI providers | Google Gemini, OpenAI, Anthropic, OpenRouter, plus an internal metered gateway (AXON) — selected per task with key rotation and provider fallback |
| Visual diffing | `pixelmatch` 0.3.0 + Pillow, plus an AI vision pass for structural differences |
| Video/PDF ingestion | Gemini Files API (direct REST) for video, `pypdf` for SOW documents, `ffmpeg`/`ffprobe` for still-frame extraction |
| Document export | `python-docx` (native `.docx`), `weasyprint` (HTML → PDF) |
| Android testing | Appium via BrowserStack App Automate (cloud device farm, no local emulator) |
| Reverse proxy | Nginx (TLS termination, routes to frontend/backend) |

---

## 2. Architecture

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
       │  :3000      │  proxy     └──────┬───────┘
       └─────────────┘                   │ enqueues jobs
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
                                      volumes: uploads,      subprocess /
                                      screenshots, .robot    BrowserStack
                                      test projects)
```

The frontend never talks to Celery, Postgres, or `robot` directly — every
mutation goes through the FastAPI backend, which writes to Postgres and, for
long-running work, enqueues a Celery task and returns immediately. The
`backend` and `celery_worker` containers share two Docker volumes: the named
volume `visual_qa_data` (uploaded SOWs/videos/screenshots/diffs) and a
**bind mount of the sibling `../automation` folder** — the literal mechanism
by which the platform finds and executes real `.robot` test suites (see
[§7 Robot Framework execution](#7-robot-framework-execution--this-is-real-not-mocked)).

---

## 3. Folder structure

```
dashboard/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, lifespan/seed, CORS, rate limiting, router mount
│   │   ├── api/v1/            # route files, one per resource (~16 files, ~106 routes)
│   │   ├── core/               # config.py, security.py, permissions.py, dependencies.py, rate_limit.py, seed.py, database.py, logging.py
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── schemas/              # Pydantic request/response schemas
│   │   ├── services/              # business logic (LLM routing, ingestion, orchestrator, etc.)
│   │   └── workers/
│   │       ├── celery_app.py       # broker/backend config, beat schedule, task registry
│   │       └── tasks/               # one module per Celery task family
│   └── alembic/versions/            # migrations, sequential 0001..0030+
├── frontend/
│   └── src/
│       ├── app/             # Next.js App Router pages + API proxy routes
│       ├── components/      # shared React components (ai-testing/, ui/)
│       ├── lib/, utils/     # apiClient, auth token store, etc.
│       └── middleware.js    # Edge auth-gate for page routes
├── docker/                  # Dockerfiles for backend/frontend/nginx
├── SOW_FEATURE_PLAN.md      # full design doc for the SOW Creation & Rewrite feature
└── docker-compose.yml
```

---

## 4. Running locally

```bash
docker compose build
docker compose up -d
docker exec <backend-container> alembic upgrade head   # also runs automatically on container start
```

Required env vars (`.env` at the repo root — copy from `.env.sample`, **not**
`.env.example`, see [§9 Known Issues](#9-known-issues--risks) for why):
`DATABASE_URL`, `JWT_SECRET_KEY`, `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD`
(seeded on first boot if no users exist), `AUTOMATION_ROOT`, plus whichever
AI provider keys the features you're using need (`GEMINI_API_KEY`/
`GOOGLE_API_KEY(S)` for Visual QA and Video Walkthrough, `OPENAI_API_KEY`/
`ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` as configured). See the full,
inline-commented list in `backend/.env.example` and `.env.sample`.

The `backend` and `celery_worker` images are **not** source-bind-mounted —
after editing backend code you must rebuild:

```bash
docker compose build backend celery_worker && docker compose up -d backend celery_worker
docker compose build frontend && docker compose up -d frontend
```

**`docker-compose.yml` uses an explicit per-service `environment:` allowlist,
not a blanket `.env` passthrough** — a variable only reaches a container if
it's both set in `.env` *and* listed in that service's `environment:` block
in `docker-compose.yml`. Adding a new env var to `.env`/`.env.example` alone
is not enough; it silently does nothing until it's also added to
`docker-compose.yml` (this exact mistake shipped once with `SOW_ENABLED` —
see [Changelog](#changelog)).

---

## 5. Feature map

Navigation (left sidebar) → what each section does:

| Nav item | Route | Purpose |
|---|---|---|
| Dashboard | `/dashboard` | Stats overview, open to any logged-in user |
| Projects | `/projects` | Project/environment/credential-profile management |
| Defects | `/defects` | Defect tracking (developers only see defects assigned to them) |
| Execute | `/execute` | Manual/deterministic test suite execution (real Robot Framework suites) |
| Reports | `/reports` | Run history, result reporting, video playback, AI-suggestion review |
| **Vibe Testing** | `/ai-testing` | AI testing suite — see [§6](#6-vibe-testing-ai-testing) |
| **SOW** | `/sow` | SOW Creation & Rewrite — see [§6](#6-vibe-testing-ai-testing) |
| Admin → Users | `/admin/users` | User & role/permission management (admin only) |
| Admin → Audit Logs | `/admin/audit-logs` | Action audit trail (admin + qa_lead) |

### Auth & RBAC

**JWT flow:**
1. `POST /api/v1/auth/login` verifies email/password (bcrypt) and
   `is_active`, then issues a short-lived **access token** (JWT, HS256,
   `ACCESS_TOKEN_EXPIRE_MINUTES`, default 15) and an opaque 64-hex
   **refresh token** (`REFRESH_TOKEN_EXPIRE_DAYS`, default 1 → 24h sessions).
2. Only the **SHA-256 hash** of the refresh token is persisted
   (`refresh_tokens.token`) — the raw value is returned once and never stored.
3. `POST /api/v1/auth/refresh` looks up the token by hash, checks
   `is_revoked`/`expires_at`, then **rotates** it (single-use).
4. `POST /api/v1/auth/logout` revokes one refresh token. There is no
   "revoke all sessions" or access-token blocklist — a stolen access token
   stays valid until its own short expiry regardless of logout (standard
   short-lived-JWT tradeoff).
5. Every protected route depends on `get_current_user`, which validates the
   bearer token (signature, expiry, `type == "access"`) and loads +
   `is_active`-checks the `User` row.

**Frontend token handling** (`src/lib/api.ts` + `src/utils/apiClient.js`,
unified as of 2026-07-15):
- **Access token: in-memory only** — never written to `localStorage`; dies
  with the tab (the actual XSS-hardening property).
- **Refresh token: httpOnly, `SameSite=Strict` cookie** (`aep_refresh_token`,
  `Path=/api/auth`) set by the Next.js proxy — client JS can never read it.
- `middleware.js` (Edge) additionally checks a separate, short-lived,
  non-httpOnly `aep_token` cookie via Web Crypto, purely so route gating
  (`/dashboard`, `/admin/*`, etc.) doesn't need a network round trip; it is
  never used to authorize an actual API call.
- On a hard reload the in-memory token is gone; `Providers.jsx`/module-load
  logic in `apiClient.js` does a silent-refresh bootstrap via the httpOnly
  cookie before the rest of the app's queries fire.

**RBAC is hybrid — permission-based for features, role-based for admin ops:**
- `UserRole` (`admin, qa_lead, qa_engineer, developer, viewer, sales, ba, hr`)
  is descriptive only — `admin` always has full access; every other role has
  zero implicit permissions.
- Real feature access comes from `User.permissions`, a JSONB list grantable
  per-user from the admin Users page. Grantable keys
  (`app/core/permissions.py`): `projects, test_suites, execute, defects,
  vibe_testing, sow`. (`test_runs` and `reports` were removed 2026-07-15 —
  they were grantable in the UI but enforced by no route; see
  [§9](#9-known-issues--risks).)
- `require_permission(key)` (admins bypass) gates feature routes;
  `require_roles(...)` (always admin-only, coarser) gates user management,
  audit-log viewing, and hard-deletes.

### 6. Vibe Testing (`/ai-testing`)

Three tabs, backed by four real feature modes (the "New" tab's UI is a
4-card mode picker over these):

| Mode | Backing component | Backend feature |
|---|---|---|
| Quick | plain goal box | goal-based `browser-use` agent run ("The Hands") |
| Visual | `AutonomousQASection` | orchestrator ("The Brain") — routes to Hands/Judge/self-execute |
| SOW | `SowCheckpointsSection` (`variant="sow"`) | spec-document → checkpoint extraction |
| Video | `SowCheckpointsSection` (`variant="video"`) | walkthrough-video → checkpoint extraction |

- **SOW Checkpoints** — upload a requirements document (`.txt`/`.md`/`.pdf`).
  The Router (`design_ingest.py`) extracts structured QA checkpoints; large
  documents are chunked (`sow_parts`) and analyzed on demand.
- **Video Walkthrough** — upload a screen recording (`.mp4`/`.webm`/`.mov`).
  Gemini's Files API watches the video and extracts checkpoints the same
  way. **Requires a declared platform/product name** (hard requirement since
  2026-07-12 — see [Changelog](#changelog)) so the model has something to
  ground its extraction against instead of guessing.
- **Visual Audit** — pixel-diff + AI vision comparison of a live page
  against a reference design (Figma export or uploaded PNG).
- **Figma Import** — pull frames directly from a Figma file as reference
  designs.
- Every functional checkpoint extracted from a SOW or video is saved
  straight to the **Skills** tab as a runnable prompt skill.
- **Results** tab — history of past AI runs (summary, step-by-step replay,
  screenshots). New Vibe Test / Skill Replay runs (web platform) also get an
  **AI Quality Score**: a post-run DeepEval (`GEval`) judgment of whether the
  agent's actual actions accomplished the goal, independent of the agent's
  own self-reported success — see [Changelog](#changelog), 2026-07-25.
- **Skills** tab — reusable skills, recorded (browser-action replay from a
  passed run) or prompt-only (SOW/video extraction). Hand-editable; manual
  edits are protected from being overwritten by re-analysis. Sortable,
  bulk-selectable (delete / assign-project / run).
- **Android testing** — stubbed "Coming soon" placeholder in the UI; no
  backend behind it yet, despite `Appium-Python-Client`/BrowserStack config
  already existing in `requirements.txt`/`docker-compose.yml` for a related,
  separate Android Vibe Testing effort.

### SOW Creation & Rewrite (`/sow`)

Runs the opposite direction from SOW Checkpoints: *meeting transcript +
recording + design references → generated SOW document*, detailed enough
(down to individual buttons/dropdowns/toggles) that the output can itself be
fed back into the checkpoint extractor above. Full design in
`SOW_FEATURE_PLAN.md` at the repo root.

**Pipeline:** attach sources (transcript paste/upload, recording upload with
size/duration caps, design PNG) → each independently extracted into a raw
`sow_requirements_ledger` → ledger grouped into sections → each section
drafted into typed `content_blocks` → independently audited for completeness
against source facts (coverage score + named gaps, not just a hope the
drafting prompt was followed) → assembled with an LLM-drafted Project
Overview/Scope of Work plus five templated trailing sections (Out of Scope,
Assumptions, Dependencies, Exclusions, Sign-off) that are **deliberately
never LLM-drafted** (hallucinating contractual scope language is unsafe) →
versioned with a genuine partial-failure model (`done_with_errors` still
shows every section that succeeded) → structured per-block-type editor for
hand-fixing sections → client-side version diff → export to `.md`/`.docx`/
`.pdf` → one-click hand-off into the Vibe Testing checkpoint extractor →
selective rewrite/patch of individual sections (regenerate only what you
pick; hand-edited sections are protected unless force-overridden).

**Status: Phases 0–7 complete** (see [Changelog](#changelog) for the full
build-out and every bug caught/fixed along the way).

### Visual QA / "Memory Bank" pattern (`app/models/visual_qa.py`)

All Visual QA source material (Figma PNGs, SOW documents, walkthrough
videos, meeting transcripts/recordings) lives in one `design_artifacts`
table, **deduplicated by SHA-256** so identical content is never re-analyzed
(or re-billed) twice. Parsed output lands in `design_rules` (one row per
artifact, JSONB checkpoints). Feature-flagged behind `VISUAL_AUDIT_ENABLED`
— every endpoint 404s when it's off.

Key tables: `design_artifacts`, `sow_parts`, `design_rules`, `visual_runs`,
`visual_findings`.

### AI test runs (`app/models/ai_runs.py`)

`ai_test_runs` / `ai_run_events` record goal-based agent runs. `ai_skills`
stores both recorded-replay and prompt-only skills, unified under one
upsert path (`app/services/skill_store.py`) keyed by `goal_hash` /
`source_key`.

---

## 7. Backend detail

### API surface (`app/api/v1/`) — ~106 routes across 16 modules

| Module | Prefix | What it covers |
|---|---|---|
| `auth.py` | `/auth` | login, refresh, logout, `me` |
| `audit.py` | `/audit` | paginated audit log — admin only |
| `dashboard.py` | `/dashboard` | `GET /stats` — every dashboard KPI in one call, optional `project_id` scope |
| `users.py` | `/users` | user CRUD, role/permission assignment (admin only), `assignable` lookup |
| `projects.py` | `/projects` | project CRUD, `discover-suites` (scans `AUTOMATION_ROOT`, auto-registers) |
| `test_suites.py` | `/projects/{id}/suites` | suite CRUD scoped to a project |
| `test_suites_list.py` | `/test-suites` | flat cross-project suite listing (raw SQL) |
| `test_results.py` | `/test-results` | individual test-case outcomes, filterable by run/status |
| `executions.py` | `/runs` | trigger/list/cancel/delete a run, reconcile a stuck run, `GET /{id}/stream` (SSE) |
| `reports.py` | `/reports` | run history/detail/export, video playback, AI-suggestion review + approval |
| `defects.py` | `/defects` | defect CRUD; developers only see defects assigned to them |
| `ai_runs.py` | `/ai-testing` | credential profiles, goal-based AI runs, saved skills, `GET /runs/{id}/stream` (SSE) |
| `visual_audit.py` | `/visual-audits` | references, Figma import, SOW/video ingestion, pixel-diff+AI audit runs — gated by `VISUAL_AUDIT_ENABLED` |
| `orchestrator.py` | `/orchestrator` | "The Brain" — routes a goal/URL/design-reference combo to Hands/Judge/self-execute |
| `sow.py` | `/sow` | SOW document CRUD, source ingestion, generate/rewrite, editor, export, hand-off — gated by `SOW_ENABLED` |
| `android.py` | `/android` | Android Vibe Testing (BrowserStack App Automate) |

Every list endpoint returns the same envelope shape: `{data, total, page,
limit}`. Full interactive docs at `/docs` once the server is running.

### Data model (`app/models/`)

**Auth/RBAC** — `users` (`role` descriptive enum, `permissions` JSONB —
the real access-control source), `refresh_tokens` (hashed token,
`is_revoked`, `expires_at`), `audit_logs` (actor, action, resource, JSONB
`details`, IP).

**Core QA domain** — `projects` (`name` unique among active rows vs.
`folder_name`, an immutable key matching `automation/` folders;
`environments` array; `product` enum defined but not yet exposed by any
route — see [§9](#9-known-issues--risks)); `test_suites` (`suite_type`:
smoke/regression/sanity/exploratory/full); `test_runs` (`celery_task_id`,
status, timing); `test_results` (one row per test case per run); `defects`
(linked to a `test_result`, severity/status enums, `assigned_to`).

**Visual QA / "Memory Bank"** — `design_artifacts` (SHA-256 deduped),
`sow_parts` (chunked SOW text), `design_rules` (merged JSONB checkpoints),
`visual_runs` / `visual_findings` (pixel-diff + vision audit results).

**AI test runs & skills** — `ai_credential_profiles` (Fernet-encrypted
login credentials scoped by allowed domain), `ai_test_runs` /
`ai_run_events`, `ai_skills` (upserted by `goal_hash` or
`(artifact_id, slug)`; `manually_edited=True` protects hand edits).

**Orchestrator** — `orchestrator_runs` (may delegate to a real
`AITestRun`/`VisualRun`), `orchestrator_step_decisions` (audit trail of
which step ran or was skipped and why).

**SOW Creation & Rewrite** — `sow_documents`, `sow_document_sources`,
`sow_document_versions`, `sow_sections` (typed `content_blocks`,
`coverage_score`/`coverage_gaps`, `edited_by_human`), `sow_requirements_ledger`
(flat fact/UI-element checklist with `source_ref` traceability),
`sow_generation_jobs`.

### Services (`app/services/`) — business logic layer

| Service | Responsibility |
|---|---|
| `auth_service.py` | login, token issue/rotate/revoke |
| `user_service.py` | user CRUD |
| `audit_service.py` | best-effort audit-log writer (never raises, never blocks a mutation) |
| `dashboard_service.py` | computes every dashboard KPI/chart via raw-SQL aggregates |
| `credential_service.py` | Fernet encryption for AI credential profiles (`AI_CREDENTIAL_KEY`) |
| `figma_service.py` | Figma REST client (stdlib `urllib`, deliberately not `requests`/`httpx`, avoids a `browser-use` dependency conflict) |
| `suite_discovery.py` | scans `AUTOMATION_ROOT` for `<project>/tests/<suite>/<suite>_tests.robot`, auto-registers `Project`/`TestSuite` rows |
| `llm_router.py` | "The Router" — `litellm`-based primary→fallback model chain with retries and strict-JSON output mode |
| `model_pool.py` | live-probes which LLM providers/keys actually work, resolves an abstract model choice into a concrete client |
| `ai_runner.py` | "The Hands" — launches headless Chromium over CDP, runs `browser-use`'s `Agent` (goal-based) or `rerun_history()` (skill replay), provider-precedence LLM selection, Google-key rotation on 429 |
| `visual_judge.py` | "The Judge" — deterministic pixel-diff (clustered into bounding boxes) + AI vision pass for structural differences only, skipping vision when pixel-diff is already conclusive |
| `orchestrator.py` | "The Brain" — deterministic rules-first routing with a full decision audit trail |
| `design_ingest.py` | SOW text extraction, chunking, per-chunk checkpoint extraction, skill-markdown rendering |
| `video_ingest.py` | Gemini Files API video digestion, still-frame extraction assist, hard-gates on `platform_match` |
| `skill_store.py` | shared upsert logic for goal-based and prompt-only skills |
| `sow_ledger.py` | SOW source extraction (transcript/recording/design → ledger facts) |
| `sow_drafting.py` / `sow_assembly.py` / `sow_audit.py` / `sow_patch.py` / `sow_export.py` | SOW section grouping/drafting, document assembly, completeness audit, section rewrite, multi-format export |
| `device_farm.py` | BrowserStack App Automate session management for Android testing |

### Background jobs (`app/workers/`)

Celery broker/backend default to `redis://localhost:6379/0`
(`redis://redis:6379/0` in Docker). `task_acks_late=True`,
`worker_prefetch_multiplier=1`, `task_soft_time_limit=1800`/
`task_time_limit=3600` (video ingestion overrides to 1200s soft limit).
**Beat runs embedded in the worker** (`-B` flag) — no separate beat
container, so scaling `celery_worker` past one replica would silently
duplicate periodic tasks.

| Task | Triggered by | What it does |
|---|---|---|
| `execute_test_suite` | `POST /runs` | Finds the matching `.robot` file under `AUTOMATION_ROOT`, spawns `robot`/`pabot` as a real subprocess with a custom `--listener`, live-parses stdout, falls back to `output.xml` |
| `reconcile_stale_runs` *(periodic, 5 min)* | Celery beat | Recovers runs stuck `running`/`queued` >10 min |
| `LiveResultListener` | invoked by `robot`/`pabot` | RF listener v3 — inserts each `TestResult` row into Postgres right after `end_test` |
| `run_ai_test_task` | `POST /ai-testing/runs` | Goal-based AI test run, live events, narrative summary, auto-saves a skill on pass |
| `replay_skill_task` | `POST /ai-testing/skills/{id}/replay` | Deterministic replay of a saved skill, AI fallback if replay fails |
| `run_visual_audit_task` | `POST /visual-audits` | Screenshots the live page, runs the Judge, persists findings |
| `ingest_sow_task` / `analyze_sow_part_task` | SOW Checkpoints upload/part-analyze | Extracts/chunks/analyzes a SOW, merges checkpoints, auto-saves skills |
| `import_figma_frames_task` | `POST /visual-audits/figma/import` | Batch-exports/downloads selected Figma frames |
| `ingest_video_task` | `POST /visual-audits/video` | Digests a walkthrough video, saves checkpoints + skills |
| `run_orchestrator_task` | `POST /orchestrator/runs` | Wraps `orchestrator.execute_run()`, force-terminates on any unhandled exception |
| `reconcile_stale_visual_qa` *(periodic, 5 min)* | Celery beat | Marks stuck `SowPart`/`DesignArtifact` rows `error` |
| `generate_sow_task` / `patch_sow_task` | `POST /sow/documents/{id}/generate` / `.../rewrite` | Full SOW generation / selective section rewrite |
| `reconcile_stale_sow_sources` *(periodic, 5 min)* | Celery beat | Recovers `sow_document_sources` stuck `processing` |

### 7. Robot Framework execution — this is real, not mocked

1. `suite_discovery.py` / `executions.py` look for
   `<project>/tests/<suite>/<suite>_tests.robot` under `AUTOMATION_ROOT`
   (bind-mounted to `/automation` in Docker from the sibling `automation/`
   folder).
2. `execute_test_suite` spawns `robot`/`pabot` as a real subprocess
   (`--outputdir`, `--pythonpath`, `--listener rf_listener.py:<run_id>`,
   `--variable BROWSER:headlesschromium`), streams stdout live, falls back
   to parsing `output.xml`.
3. `rf_listener.py` is a genuine Robot Framework Listener API v3 class that
   inserts a `TestResult` row into Postgres right after each test ends.
4. `reports.py` reads real files back out of the mounted automation folder
   — recorded videos, AI locator-repair suggestions.
5. The stale-run reconciler (on-demand and periodic) exists specifically to
   recover from real subprocess/worker crashes.

### Config & environment variables

Only ~14 vars are validated through the typed `Settings` class
(`app/core/config.py`); everything else (Visual QA/AI/Figma/SOW tuning) is
read ad hoc via `os.environ.get(...)`, with duplicated defaults in a few
places (see [§9](#9-known-issues--risks)). Full, current, inline-commented
list: `backend/.env.example`. Load-bearing vars:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `JWT_SECRET_KEY` | yes | JWT signing secret |
| `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` | yes | seed admin, first boot only |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | no | Celery/Redis, defaults to `redis://localhost:6379/0` |
| `AUTOMATION_ROOT` | yes, for Execute | path to the `automation/` folder |
| `CORS_ALLOWED_ORIGINS` | no | only for direct browser→FastAPI calls (not the current deployment mode) |
| `VISUAL_AUDIT_ENABLED` | no (off if unset) | master switch for all Visual QA / orchestrator routes |
| `SOW_ENABLED` | no (off if unset) | master switch for all `/sow/*` routes — **must also be added to `docker-compose.yml`'s allowlist**, see [§4](#4-running-locally) |
| `AI_CREDENTIAL_KEY` | no, but should be set in production | Fernet key for AI credential profiles — **falls back to an ephemeral in-memory key if unset**, so any profile saved before a restart becomes permanently undecryptable after one |
| `GEMINI_API_KEY(S)` / `ANTHROPIC_API_KEY(S)` / `OPENAI_API_KEY(S)` / `OPENROUTER_API_KEY` / `AXON_API_KEY` | at least one, for AI features | LLM provider keys; plural variants accept a comma list for key rotation |
| `FIGMA_API_TOKEN` | only for Figma import | read-scope personal access token |
| `BROWSERSTACK_USERNAME` / `BROWSERSTACK_ACCESS_KEY` | only for Android testing | cloud device farm credentials |

### Database migrations

30+ sequential migrations (`0001`..`0030+`). Run automatically on every
container start (`alembic upgrade head && uvicorn ...`) — not a separate
one-shot job.

```bash
cd backend
alembic revision --autogenerate -m "describe your change"
alembic upgrade head          # requires: CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

---

## 8. Frontend detail

### Routing / pages (`src/app/`, App Router)

Every route is a client component (`"use client"`) wrapped in `<AppShell>`.
There is no server-side data fetching — TanStack Query on the client does
all of it. Every route has a matching `loading.jsx`/`error.jsx`.

### API proxy layer (`src/app/api/`)

Nothing in the frontend talks to Postgres or holds business logic —
`src/app/api/utils/sql.js` is a deliberate stub that throws if anything
tries to import a SQL client. Almost every route under `src/app/api/` is a
thin proxy through `proxyToFastAPI()` (`src/app/api/utils/proxy.js`) to
`${FASTAPI_URL}/api/v1/...`. A catch-all route (`api/v1/[...path]/route.js`)
forwards any other `/api/v1/*` path verbatim.

**Why the proxy layer exists:**
- **Auth/cookie handling** — strips the refresh token out of FastAPI's JSON
  response and re-sets it as the httpOnly `aep_refresh_token` cookie.
- **Body-size correctness** — `middleware.js` excludes `/api/*` from its
  matcher because Next.js Edge Middleware's 10MB body cap was silently
  truncating multipart video uploads before FastAPI ever saw them.
- **Response cleanup** — strips hop-by-hop/encoding headers that would
  otherwise cause `ERR_CONTENT_DECODING_FAILED` in the browser.

Two routes stream **Server-Sent Events**: `execute/[id]/stream` and
`ai-testing/runs/[run_id]/stream`. Both accept the JWT as `?token=` (since
`EventSource` can't set custom headers) and forward it as
`Authorization: Bearer` upstream.

### State management / data fetching

**TanStack Query v5** dominant pattern (`staleTime: 5min`, `cacheTime:
30min`, `retry: 1`, `refetchOnWindowFocus: false`). No Context/Redux for app
state — the current user profile lives in `localStorage`
(`authStore.js`), re-read per component.

**Auto-refresh:** Dashboard stats poll every 30s (lowered from 10s to cut
Neon cold-start load); AI Testing Results tab polls every 15s; Execute page
and AI Testing's live run view use SSE, not polling.
`AutonomousQASection.tsx`/`SowCheckpointsSection.tsx` poll via plain
`setInterval` (2s/3s) instead of TanStack Query's `refetchInterval` —
inconsistent with the rest of the app, not currently broken.

### Components

- `AppShell.jsx` — sidebar/topbar shell, nav filtered by `user.permissions`.
- `Providers.jsx` — the single `QueryClientProvider`.
- `AutonomousQASection.tsx` — combined orchestrator-run form (goal + live
  URL + environment + Figma/video/SOW dropzones + credential profile),
  submits to `POST /api/v1/orchestrator/runs`, renders live engine-status
  cards (THE BRAIN / THE HANDS / THE JUDGE / THE LINE / MEMORY BANK).
- `SowCheckpointsSection.tsx` — one component, two variants (`sow`/`video`).
- `FigmaImportSection.tsx`, `VisualAuditSection.tsx` — fully built but not
  currently wired into any page (removed from `/ai-testing` "per product
  decision" during the mode-picker rework; files remain in the tree).
- `src/components/ai-testing/` — `ModeSelector.tsx`, `shared.tsx`,
  `ResultsTab.tsx`, `SkillsTab.tsx`, `SkillDetailModal.tsx`, `RunDetail.tsx`,
  `OrchestratorRunDetail.tsx`.
- `src/components/ui/` — shadcn-CLI-managed primitives wrapping
  `@base-ui/react`.

### Styling

Tailwind v3 with oklch tokens in `src/app/global.css` (the stylesheet
`layout.jsx` actually loads). A second stylesheet, `src/index.css`,
duplicates similar tokens and is what `components.json` points the shadcn
CLI at, but it's never imported — running `npx shadcn add ...` writes into
a file nothing loads (see [§9](#9-known-issues--risks)). Dark-mode tokens
exist on several components but there is no `darkMode` strategy configured
and nothing ever applies a `.dark` class — unreachable today. Two styling
conventions coexist: legacy pages use inline `style={{}}` with hardcoded
hex; newer AI-testing surfaces use Tailwind + tokens.

### Frontend environment variables

| Variable | Where used | Purpose |
|---|---|---|
| `FASTAPI_URL` | all server-side proxy routes | backend base URL, default `http://backend:8000` |
| `SECRET_KEY` / `AUTH_SECRET` | `middleware.js`, `api/utils/auth.js` | HMAC secret for the app's own JWT verification — must match FastAPI's signing secret |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | proxy routes | token lifetimes, must match backend values |
| `NODE_ENV` | login/refresh routes, logger | gates adding `Secure` to the refresh cookie in production |
| `LOG_LEVEL` | `api/utils/logger.js` | default `info` |

`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_API_BASE_URL` are defined in places but
read by no live code path (dead config).

---

## 9. Known issues & risks

Consolidated from prior backend/frontend documentation passes plus this
review. Ordered roughly by severity.

### 🔴 Critical — action needed now

1. **The root `.env.example` file, as it exists in this folder right now,
   contains a real, non-placeholder Neon PostgreSQL connection string**
   (full host, username, and password) — not a historical artifact, this is
   the current file on disk. `backend/.env.example` and `.env.sample` are
   both correctly placeholder-only, so the fix pattern already exists; only
   the root `.env.example` was missed.
   **Do this now:** replace the `DATABASE_URL` line in `.env.example` with a
   placeholder (copy the pattern from `.env.sample`), and **rotate the Neon
   credential** regardless — if this repo has ever been pushed anywhere
   (GitHub, a shared drive, a zip sent over Slack), that credential should be
   treated as already exposed. There is no `.gitignore` file anywhere in the
   repo root, so if this folder is ever put under version control without
   one being added first, `.env` (the file with your live keys) would be
   committed right alongside it.
2. **`AI_CREDENTIAL_KEY` silently falls back to an ephemeral, process-local
   key if unset** (`credential_service.py`) — any AI credential profile
   ("Vibe Testing" saved logins) saved before a container restart becomes
   permanently undecryptable after one, with only a startup log warning, no
   hard failure. Fix options: fail fast at startup if this key is unset and
   Vibe Testing is enabled, or persist/generate it once and store it
   durably. Currently neither is done.

### 🟠 High — should fix soon

3. **No centralized error handling.** The same
   `try/except HTTPException: raise / except SQLAlchemyError: rollback();
   raise 500` block is hand-copied into dozens of route functions instead of
   a small number of FastAPI exception handlers on `app` itself. Not a bug
   today, but every future route is one copy-paste mistake away from an
   unhandled 500 or an uncommitted transaction.
4. **Two parallel config systems on the backend.** The typed `Settings`
   class validates ~14 vars; 25+ more (all Visual QA/AI/Figma/SOW tuning)
   are read ad hoc via `os.environ.get(...)` with no central validation and
   inconsistent duplicated defaults (e.g. `VISUAL_VIDEO_MAX_MB` defaults
   differ between `visual_audit.py` and the `.env.example` files). A typo in
   an env var name here fails silently instead of at startup.
5. **`langchain-openai`'s own SSRF/DNS-rebinding CVE (GHSA-r7w7-9xr2-qq2r)
   is not fixed** in the pinned version (`requirements.txt`'s own comment
   documents this) — closing it would require an incompatible major-version
   bump and an `ai_runner.py` rewrite. Currently tracked only as a code
   comment, not a ticket.
6. **`middleware.js`'s comment is actively misleading.** It claims "API
   routes handle their own auth via requireAuth/requireRole," but
   `src/app/api/utils/auth.js`'s `requireAuth`/`requirePermission`/
   `requireRole` are fully implemented and **never called by any route** —
   every proxy route just forwards the `Authorization` header and lets
   FastAPI enforce everything. Not exploitable on its own (FastAPI does
   enforce), but the next engineer who trusts that comment will ship an
   unprotected route believing something else guards it.
7. **`seed.mjs` at the frontend root** (gitignored, not committed) still
   contains a commented-out but plaintext Neon connection string plus a
   hardcoded admin email/password, used for manual local seeding. Delete it
   or scrub the real values, and rotate them if they were ever active.

### 🟡 Medium — real but not urgent

8. **`app/models/project.py`'s `Product` enum** is a real column, but no
   schema field or route ever sets/exposes it — looks like a half-wired
   feature. Confirm with the team whether it's planned or safe to drop.
9. **`requirements.txt` lists `Pillow==12.2.0` twice** — harmless (pip
   dedupes) but a one-line cleanup.
10. **`langchain-anthropic` and the bare `anthropic`/`openai` SDKs are
    imported directly in `ai_runner.py`** but not declared in
    `requirements.txt` — they currently arrive transitively via
    `browser-use`'s own pins. A future `browser-use` bump could silently
    change these versions with nothing in this project's own requirements to
    catch it.
11. **`/health`'s hardcoded `"version": "1.0.0"`** doesn't match
    `FastAPI(version="0.2.0")` in the same file. Pick one source of truth.
12. **No separate Celery beat container.** Beat runs embedded in the single
    `celery_worker` replica (`-B` flag). Scaling `celery_worker`
    horizontally would silently duplicate all three periodic reconciliation
    tasks. Needs a dedicated beat service before ever scaling workers.
13. **Frontend: a long list of declared-but-unused npm dependencies**
    (`recharts`, `zustand`, `sonner`, `motion`, `react-hook-form`, `yup`,
    `date-fns`, `classnames`, `cmdk`, `@tanstack/react-table`, `lodash-es` —
    the last has a dedicated `transpilePackages` entry in `next.config.js`
    for a package nothing imports). Prune or document as reserved.
14. **`components.json` points the shadcn CLI at `src/index.css`**, but the
    app loads `src/app/global.css`. Running the CLI to add a component today
    silently writes into a file with no effect.
15. **Dead frontend files worth pruning**: `src/utils/proxy.js` (a second,
    unused `proxyToFastAPI` reading an env var defined nowhere),
    `src/app/api/utils/upload.js` (posts to a hardcoded placeholder domain
    `api.anything.com`), `src/app/api/utils/create.js` (generic `/api/db/*`
    starter-template helper), `src/app/api/utils/audit.js` (never called —
    the real audit log is written by FastAPI). `VisualAuditSection.tsx`/
    `FigmaImportSection.tsx` are real but currently unreachable from any
    page.
16. **Dark mode is unreachable** — tokens and `dark:` variants exist on
    several components, but there's no `darkMode` strategy and no toggle.
17. **`tailwind.config.cjs` is ~1600 lines**, almost entirely an unused
    Google Fonts `fontFamily` map (only Inter is used). Safe to trim.
18. **Test tooling is fully scaffolded but 0% adopted** —
    `vitest.config.ts` references a `test/setupTests.ts` that doesn't exist,
    there's no `test` npm script, and there isn't a single test file in
    either the frontend or backend. For a QA platform, this is the most
    notable gap in the project.

### ⚪ Informational

19. **The Android testing tab in the UI is a stubbed placeholder** with no
    backend wired to it yet, even though device-farm dependencies already
    exist in `requirements.txt`/`docker-compose.yml` for a related, separate
    effort — don't confuse the two.
20. **Two styling conventions coexist** (inline `style={{}}` on legacy
    pages vs. Tailwind + tokens on newer surfaces) — not a bug, but worth
    normalizing during any future redesign pass.

### Fixed and worth knowing about (don't re-report these)

- Rate limiting was previously missing on `/api/auth/login` at the Next.js
  proxy layer — fixed (5 attempts/15 min/IP, `429` + `Retry-After`).
- Two competing frontend API-client/token-storage implementations were
  unified onto the in-memory + httpOnly-cookie model described in
  [§6 Auth & RBAC](#auth--rbac).
- The access token was previously persisted to `localStorage` (XSS risk) —
  moved to in-memory-only.
- `docker-compose.yml` was missing `SOW_ENABLED`/`SOW_MAX_RECORDING_MB`/
  `SOW_MAX_RECORDING_MINUTES` in its allowlist even though they were in
  `.env` — fixed; see [§4](#4-running-locally) for why this class of bug is
  easy to reintroduce.
- `test_runs`/`reports` were grantable permissions enforced by no route —
  removed from the grantable set rather than left as a false sense of
  access control.

---

## 10. Changelog

Condensed, dated summary of substantive changes. Older entries carried more
verbose "verified/not verified" build notes in prior versions of this
document; the underlying facts are preserved here, the verification detail
is trimmed for readability.

**2026-07-25 — AXON-primary AI routing.** AXON (`axon/gemini-flash-latest`)
is now the default provider for every router-backed AI call: browser-use
Hands, Autonomous QA's coordinator/Judge choices, Visual QA, and SOW text
work. Gemini is retained as the secondary/light fallback. The Hands client
also falls back to the resolved Gemini key pool mid-run if AXON returns an
auth, rate-limit, or exhausted-budget response (HTTP 401/402/403/429), so an
in-progress test remains recoverable. Video walkthrough ingestion remains
direct Gemini because it relies on Gemini's Files API, which the
OpenAI-compatible AXON gateway does not expose.

**2026-07-25 — Vibe Testing: resilient Gemini key failover and long-run
budget.** The Hands agent now treats Gemini 401/403/429 errors consistently
even when LangChain/browser-use wraps them as a generic LLM error, rotates to
the next configured `GOOGLE_API_KEYS` key, and cools rejected/exhausted keys
for `GOOGLE_KEY_COOLDOWN_S` (six hours by default) so later steps do not keep
retrying a known-bad key. It also validates every configured key at startup,
rather than incorrectly discarding the whole pool when its first key is
invalid. New Vibe Test tasks now have their own configurable six-hour soft
limit plus a five-minute cleanup window (`VIBE_TEST_SOFT_TIME_LIMIT_S` /
`VIBE_TEST_HARD_TIME_LIMIT_S`), replacing the inherited 30/60-minute Celery
limit while leaving all other task types unchanged. AXON remains the final
fallback when every Gemini key is unavailable.

**2026-07-25 — Vibe Testing: AI Quality Score (DeepEval).** New Vibe Test /
Skill Replay runs (web platform) are now scored post-run by DeepEval's
`GEval` metric — a second, independent judgment of whether the agent's
*actual* recorded actions accomplished the stated goal, rather than trusting
the agent's own self-reported pass/fail. Implementation: new
`app/services/ai_eval.py`, called from `_persist_result`
(`app/workers/tasks/ai_execution.py`) right after the existing narrative
summary, gated to web-platform `ai`/`skill_replay` runs with a terminal
passed/failed status (Android and Autonomous QA are unaffected by
construction — a separate persistence path). A custom `DeepEvalBaseLLM`
wrapper routes the judge call through the existing `llm_router` (same
primary→fallback chain and AI Usage cost logging already used elsewhere) —
no separate `OPENAI_API_KEY` needed. `deepeval` is pinned to `3.3.9`, not
latest (`4.x` requires `pydantic>=2.11.7`, which conflicts with this
project's `pydantic==2.10.4` pin used across every schema in the app) —
verified via a full dependency-resolution dry-run against every existing
pin before adding the line, and the exact `GEval`/`DeepEvalBaseLLM` calling
convention was verified against the pinned version directly (by installing
it and inspecting source), not against DeepEval's docs, which describe the
newer, incompatible 4.x API. `ai_test_runs` gained four nullable columns —
`eval_score`, `eval_reason`, `eval_status`, `eval_metric` (migration
`0033_ai_run_eval`) — surfaced on `GET /ai-testing/runs/{id}` and shown as a
new "AI Quality Score" card in the Results tab detail view (`RunDetail.tsx`),
rendered only when a score exists so every pre-existing run is unaffected.
Best-effort by design, same contract as the narrative summary: any failure
(deepeval unavailable, every LLM in the router chain failing, an unparseable
judge response) is caught and logged, never blocks run persistence.
Verified end-to-end against the running dev stack: real migration applied
cleanly, backend/Celery start with no import errors, and `evaluate_run()`
correctly discriminated a genuine pass (scored 1.0) from a run whose summary
falsely claimed success despite a CAPTCHA-blocked trajectory (scored 0.0) —
both via real calls through the configured LLM provider.

**2026-07-25 — New Vibe Test: live browser view + recorded video, screenshots
removed.** "New Vibe Test" (Quick mode) and Skill Replay now show a genuinely
live view of the browser while the AI agent drives it, and attach a
downloadable full-session recording to the result — replacing the old
discrete-screenshot live panel and Screenshots tab entirely for these two
flows. Implementation: a second CDP session (`Page.startScreencast`) on the
page `ai_runner.py` already has open, fanned out to (1) a Redis pub/sub
channel relayed to the browser over a new `/runs/{id}/live-frames` SSE
endpoint for the live view, and (2) an `ffmpeg` subprocess (`image2pipe` →
H.264 mp4) for the recording, served via a new `/runs/{id}/video` endpoint
(`FileResponse`, Range-seekable) with a Download button. Both ffmpeg and the
`redis` client were already present, so this needed no new system deps.
Gated behind a new `enable_live_capture` opt-in threaded through
`ai_runner.py`'s shared execution functions, defaulting to `False` — the
Autonomous QA orchestrator's Hands sub-step and Android runs are byte-for-byte
unaffected and keep taking per-step screenshots as before. `ai_test_runs`
gained one nullable `video_path` column (migration `0032_ai_run_video`); a
run's recording is deleted from disk when its history row is deleted.

**2026-07-20 — SOW Creation & Rewrite, Phases 0–7 (full feature build-out).**
Shipped, in order: document CRUD + schema (Phase 0) → source ingestion into
a raw requirements ledger (Phase 1, plus a same-day `docker-compose.yml` fix
after `SOW_ENABLED` turned out not to reach the container despite being set
in `.env`) → drafting + assembly + generation pipeline (Phase 3, with two
atomicity/idempotency bugs caught and fixed during self-review: a two-commit
sequence that could strand a document in `'generating'` forever, and a
missing idempotency guard that would have duplicated sections on a Celery
task redelivery) → independent completeness audit + coverage badges (Phase
4) → structured block editor + version diff (Phase 5) → export to
`.md`/`.docx`/`.pdf` + one-click hand-off into the Vibe Testing checkpoint
extractor (Phase 6, plus a Debian package-name fix for `weasyprint`'s system
dependencies) → selective section rewrite/patch (Phase 7, with a
row-lock-ordering race condition caught and fixed: the endpoint originally
validated target sections before acquiring its concurrency lock, which a
concurrent `/generate` could have raced).

**2026-07-16 — Vibe Testing "New Test" panel: SSE token, 401 noise,
Environment/Credential ordering.** Fixed four bugs: live-run SSE never
progressed past "Initialising…" because it read the access token from
`localStorage`, which stopped being populated when the token moved to
in-memory-only storage; a burst of 401s on every page load because the
in-memory token wasn't available before the first render's queries fired
(fixed by moving the refresh-cookie redemption to module-load time); the
Credential Profile picker could be filled before an Environment was chosen;
and there was no way to ad-hoc test a URL without a pre-seeded Environment
(added a synthetic "No Environment" option).

**2026-07-16 — Bypass credential profile investigation.** A cookie-based
login bypass profile injected its auth cookie without error but the app
still showed the public homepage instead of the dashboard. An initial fix
(requiring email/OTP on the bypass endpoint) was diagnosed as wrong and
fully reverted the same day. Root cause is still open — candidates include
the profile's stored target URL, a cookie domain/name mismatch, or how the
target app reads the cookie.

**2026-07-12 — Video Walkthrough: mandatory platform name + anti-hallucination
guard.** The feature was extracting checkpoints that matched an uploaded
SOW instead of actual video content — root cause was an uploaded test video
that happened to be a recording of the SOW panel itself, not a linking bug.
Fixed by requiring a declared `platform_name` on every video upload (422 if
missing) and hard-failing ingestion if Gemini's on-screen content doesn't
match what was declared. A same-day follow-up fixed two problems in that
fix itself: the mismatch notice was being saved as a runnable skill (never
should have looked like a normal finding), and the mismatch verdict was
initially wrong due to native video understanding under-weighting small
persistent UI chrome — fixed by attaching extracted still JPEG frames
alongside the video for the model to check first.

**2026-07-12 — Skills tab: sorting + bulk actions.** Added `sort_by`/
`sort_dir` to `GET /ai-testing/skills`, plus `bulk-delete` and
`bulk-assign-project` endpoints with checkbox multi-select in the UI.

**2026-07-15 — Security/cleanup pass.** Rotated the exposure pattern for
committed Neon credentials (see [§9](#9-known-issues--risks) for what's
still outstanding), unified the two competing frontend token-storage
implementations, moved the access token off `localStorage`, added rate
limiting to the frontend login proxy, and removed the two dead
(never-enforced) `test_runs`/`reports` permission keys.

*For exhaustive per-change verification notes (exact test commands run,
what was and wasn't confirmed against a live environment), consult version
history / prior commits — this file now keeps only the durable facts.*
