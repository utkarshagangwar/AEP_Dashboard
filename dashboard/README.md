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
`SowCheckpointsSection.tsx`/`VisualAuditSection.tsx` poll via plain
`setInterval` (2s/3s) instead of TanStack Query's `refetchInterval` —
inconsistent with the rest of the app, not currently broken.

### Components

- `AppShell.jsx` — sidebar/topbar shell, nav filtered by `user.permissions`.
- `Providers.jsx` — the single `QueryClientProvider`.
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

**2026-08-10 — SOW page: extraction loaders, nested live status, collapsible
version picker.** All frontend; no API, schema, or worker change.

1. *Attached sources status cell* (`app/sow/[id]/page.jsx`,
   `SourceProgressCell`). The 5px blue bar is now a 22px light-blue capsule
   with an inset amber fill and a walking turtle riding the fill's leading
   edge — so the mascot's position IS the percentage rather than a decoration
   on a timer. The no-denominator mode (recording, design image, file read
   before chunking) sweeps instead of filling and carries no turtle: there is
   no finish line for it to walk toward. It also drops its `aria-valuenow`
   rather than inventing one, which is ARIA's own signal for indeterminate.

2. *Live extraction status* (`components/SowExtractionProgress.tsx` +
   `workers/tasks/sow_ingest.py`), four fixes:
   - **Endless spinner.** The worker opened the `read` and `part` stages with
     a `running` event and never closed them, so those rows spun forever —
     including long after the run ended. It now emits `read`/DONE and
     `part`/DONE on the success paths.

     It **emits** a closing event rather than editing the row that opened the
     stage, and that is not a style choice: `visual_audit.get_sow_progress`
     filters `sequence > after`, so the panel only ever receives rows it has
     not read. An in-place update would be invisible to every client already
     past that sequence — i.e. every client that saw the stage start. The UI
     folds a closing DONE onto the opening row (`buildGroups`) so the
     timeline still reads "Reading part 3 of 15" once, ticked, instead of
     doubling in length. A closing ERROR resolves the row but is still drawn,
     since its description is the failure message.

     `resolveStatus` survives as the fallback for what no emit can cover: a
     worker killed mid-stage runs no code, so it writes no closing event and
     no error event either. Such a run is detected via the artifact's own
     `parse_status` (an independent failure signal — the timeline can be all
     successes) and its dead stage renders `stalled`, an amber dash, rather
     than a tick claiming work that never happened. Header rows settle on a
     later GROUP, not a later event: a header stands for the whole part, and
     settling it on its own first sub-step would leave nothing spinning
     through the long extraction call. A group whose child errored inherits
     the failure, since a failed part returns early and emits no close.
   - **Nesting.** Consecutive events sharing a `part_number` are one group —
     first is the header, the rest indent under it with a hairline connector.
     Previously a part's sub-steps read as peers of the part itself. Groups
     nest *only* where the backend actually opened a containing stage: a
     single-part document emits no `part` event at all (it is guarded on
     `total_parts > 1`), so its steps render flat rather than hanging off
     whichever step happened to come first.
   - **Fixed height.** The list is a 256px internal scroller (same reasoning
     as the ledger table) that follows the run, but only for a reader already
     at the bottom. It used to grow the page under the reader for the whole
     run — eighty-odd rows on a twelve-part document.
   - **Alignment.** Markers sit on the text centreline (`items-center`).
   - The `running` marker is now a metaball orb (seven blurred polygons in an
     SVG mask, snapped by `contrast()`), replacing `Loader2`.

3. *Versions* (`app/sow/[id]/page.jsx`). Now a collapsed-by-default band
   shaped like the Rewrite panel above it, wrapping the version picker **and
   the version's own content** — the "Generated by …" line and every section
   card. Collapsing only the picker split one thing across two boxes: a shut
   "Versions" band with the version's contents still spilling out below it,
   belonging to nothing on screen. The picker's original two-column layout is
   unchanged inside the body; the header names the selected version so the
   closed state loses no information.

Loader CSS lives in `app/global.css` (`.sow-capsule`, `.sow-orb`) rather than
per-component `<style>` tags, since both repeat per row/step. Both hold a
still pose under `prefers-reduced-motion`. One trap worth knowing:
`--sow-orb-scale` must stay **unitless** — it is the argument to `scale()`,
and a px value (or a calc dividing one) invalidates the whole transform and
silently leaves a 100×100 orb on the row.

**2026-08-10 — "Visual and design QA" mode removed from Vibe Testing.** The
third mode card on `/ai-testing` → New Test (the combined live-site + Figma +
walkthrough-video + spec-doc + saved-reference Autonomous QA audit) is gone
per product decision. `ModeSelector`'s `TestMode` is now `"ui" | "functional"`
and `frontend/src/components/AutonomousQASection.tsx` was deleted (it had no
other consumer). Deliberately **not** removed: the orchestrator backend
(`/api/v1/orchestrator/*`, `app/services/orchestrator.py`, its Celery tasks)
and `OrchestratorRunDetail`/`FindingCard` on the frontend — existing
orchestrator runs still open from the Results tab, so removing those would
break run history. Nothing else on the page changed: UI Test, Functional
Test, Android testing, Results, Coverage, and Skills are untouched.

**2026-08-10 — MKV walkthroughs, nginx upload caps, and whole-surface hover
targets on the SOW pages.**

1. *`.mkv` accepted everywhere video is.* Added to `visual_audit.
   _VIDEO_EXTENSIONS` (its EBML magic is the same as WebM's, so
   `_looks_like_video` just treats the two together — `_WEBM_MAGIC` renamed
   `_MATROSKA_MAGIC` to say so), to `sow_ledger._RECORDING_MIME_BY_EXT`, and
   to the four frontend accept lists that post to those endpoints
   (`ImportSowDialog`, `SowCheckpointsSection`, `AutonomousQASection`,
   `AttachSourcesFolder`).
2. *…but converted before analysis.* Gemini's Files API supports
   mp4/mpeg/mov/avi/flv/mpg/webm/wmv/3gpp — **not** Matroska, so a .mkv
   cannot be uploaded as-is. Relabelling it `video/webm` was rejected as the
   cheap fix: WebM permits only VP8/VP9/AV1, while .mkv in practice (OBS's
   default) carries H.264/HEVC, so the mislabel would fail mid-decode or
   silently digest a partial stream. New `video_ingest._convert_to_mp4()`
   runs `ffmpeg -c copy` first (stream copy, no re-encode, seconds even at
   500MB) and falls back to an H.264/AAC transcode only when the codecs
   can't live in MP4. `_prepare_for_upload()` wires it into both
   `digest_video` and `extract_ledger_from_recording`; the converted file is
   scratch and is deleted in `finally`, with the original .mkv remaining the
   artifact of record. ffmpeg is elsewhere a best-effort dependency (still
   frames degrade silently without it) — for .mkv it is required, so failure
   is an explicit `IngestError` telling the user to re-save as .mp4.
   `docker/Dockerfile.backend` already installs it.
3. *nginx upload caps — a pre-existing bug, and a correction to yesterday's
   entry.* The 2026-08-10 entry below claimed no reverse proxy capped the
   body size. That was wrong: `docker/nginx.conf` sets no
   `client_max_body_size`, so nginx's 1MB default applied to the whole API,
   and nginx **is** in the compose stack. Every upload endpoint has been
   413'ing above 1MB regardless of its backend cap — SOW import (15MB),
   screenshots (10MB), transcripts, design images, APKs (200MB). Raised the
   `/api/` block to 256m, and added a nested regex location for the two
   large media routes (`/api/v1/visual-audits/video` and
   `…/sources/recording`) at 512m with `proxy_request_buffering off`, so a
   500MB walkthrough streams straight through to FastAPI instead of being
   spooled to nginx's disk first — which would otherwise defeat the
   streaming size check added earlier the same day. Verified by running the
   config in an `nginx:alpine` container: syntax passes, and the four URI
   shapes route as intended (video and recording to the upload block,
   `existing-sow` and `/api/v1/projects` to the general block, `auth/login`
   still to its rate-limited block).
4. *Meeting-recording upload streamed too.* `POST /api/v1/sow/documents/
   {id}/sources/recording` had the same shape the video endpoint just lost:
   `content = await file.read()` *before* the size check, so at the 300MB
   `SOW_MAX_RECORDING_MB` default (since raised to 500 — see below) one
   upload cost 300MB of resident memory
   and an over-cap upload was fully resident by the time it was rejected —
   the cap protected storage, not the process. Now chunked to a temp file
   with a running cap and an incremental sha256. This one got *simpler*
   rather than more complex: the duration check already needed the recording
   on disk for ffprobe, so streaming removed a write instead of adding one.
   `_ensure_artifact_file` gained a `source_path` alternative to `content` so
   its rare missing-file heal doesn't pull 300MB back into memory and undo
   the point; the three existing byte-based callers are untouched.
   Covered by `tests/test_sow_recording_upload_streaming.py` (6 tests:
   bounded reads, early cutoff, no temp-file leak on 413, 400-not-413 on
   empty, chunked hash equals whole-file hash, extension carried to the temp
   file for ffprobe) plus 3 added to
   `test_sow_artifact_file_recovery.py` for the `source_path` branch. The two
   central guards were confirmed to fail against the old implementation, so
   they are regression tests rather than decoration.

   `SOW_MAX_RECORDING_MB` then raised 300 → 500 so both video uploads in the
   product share one number — a walkthrough and a meeting recording are both
   "upload a video" to a user, and two different caps is a trap. Changed in
   `sow.py`, `docker-compose.yml`, `backend/.env.example` and the local
   `.env` (which had an *active* `=300` that would otherwise have overridden
   the new default). Still inside nginx's 512m media block.
5. *Hover affordance on whole-surface targets.* The SOW library's title cell
   and the SOW detail page's collapsible headers both used
   `.link-hover-underline`, which underlined the text on hover. On a target
   that is really an entire cell or header row, an underline points at the
   glyphs and misstates where the hit area is. Replaced with `.cell-link`
   and `.section-toggle`: a `color-mix` surface tint on the same recipe as
   the existing `.row-interactive`, plus the label deepening from 82% to
   full contrast. `.link-hover-underline` is retired (it had exactly these
   two call sites). Two details worth keeping: `.cell-link` is
   `position: absolute; inset: 0` because a table cell has no definite
   height for a child's `height: 100%` to resolve against, so an in-flow
   link left dead strips above and below where the row was taller; and its
   tint lives on a `::before` because `border-radius` clips pointer hit
   testing as well as painting, which would have made the cell's four
   corners unclickable — square box for hits, rounded plate for the eye.
   Verified in the browser: all four corners plus the centre hit-test to the
   link, and navigation and the collapse toggles still work.

**2026-08-10 — Import SOW dialog: field heights aligned, walkthrough limit
raised to 500MB and the upload made streaming.** Three changes:

1. *Field heights.* The dialog's first three rows (SOW document / Title /
   Project) rendered at three different heights. Two causes: the attached-
   document chip sized itself from `py-2` + a 24px remove button (42px)
   instead of matching the 36px empty-state button, and `SelectTrigger`'s own
   height comes from the arbitrary variant `data-[size=default]:h-8`, which
   `twMerge` cannot see as conflicting with a plain `h-9` override — so the
   Project select silently stayed 32px. Fixed locally in
   `ImportSowDialog.tsx` (`h-9` on the chip, `data-[size=default]:h-9` on the
   trigger) rather than in `components/ui/select.tsx`, so no other Select in
   the app changes size. Verified in the browser: all three rows measure 36px
   in both the empty and filled states.
2. *Walkthrough size limit → 500MB.* Was three different numbers —
   `VIDEO_MAX_MB = 50` in the dialog, `VISUAL_VIDEO_MAX_MB` defaulting to
   `50` in `visual_audit.py` and to `100` in `docker-compose.yml` (the exact
   inconsistency flagged as High #4 above), plus a commented `=100` hint in
   `backend/.env.example`. All four are now 500; the env var still overrides.
   *(Correction, same day: this entry originally said no reverse proxy caps
   the body size. `docker/nginx.conf` does — see the nginx item in the entry
   above, which fixes it.)*
3. *Video upload no longer buffered in memory.* `POST /api/v1/visual-audits/
   video` did `content = await file.read()` **before** checking the size, so
   raising the cap to 500MB would have meant 500MB of RAM per concurrent
   upload — a few of them would OOM the API container. It now streams the
   body to a `.part` temp file inside the video data dir in 1MB chunks,
   hashing as it goes and raising 413 the moment the running total passes the
   cap, then `os.replace`s the temp file onto the sha-named final path (same
   filesystem, so an atomic rename, not a second copy). The temp file is
   unlinked on any error and on the dedupe hit. Memory is now flat at one
   chunk regardless of file size. Behavior is otherwise unchanged: same
   sha256 dedupe, same magic-byte check (now against the first 16 bytes
   captured during streaming), same 202 + Celery enqueue.

Screenshots were already multi-select (`multiple` on the input, appended
across successive picks) — confirmed, not changed.

**2026-08-09 — Vibe Testing page brought onto the shared canvas texture and
heading style.** Two drifted differences, both on `ai-testing/page.tsx` only:

1. All four of the page's render states (`New Test`, `Results`/`Coverage`/
   `Skills`, the live-run two-pane view, and the completed-run summary) wrap
   their content in a full-height local div. Three of the four had their own
   `bg-gray-50` on that wrapper, painting over the app-wide grid+glow canvas
   texture from `body` (see 2026-08-08's canvas-texture entry) — every other
   page in the app renders straight onto `AppShell`'s transparent content
   area, so this one page alone stayed flat white/gray no matter the theme.
   Removed `bg-gray-50` from those three. The fourth state — the live action
   log + browser-frame view — **keeps** its `bg-white` deliberately: the log
   panel has no background of its own, and PRODUCT.md is explicit that
   evidence/audit surfaces stay "precise and sober" while loading/empty/
   onboarding states carry the mascot's texture. Texturing that panel would
   have put the grid pattern directly behind live step rows, which is a
   legibility/register regression, not a fix — flagged with a comment at the
   call site rather than silently left inconsistent.
2. The page's `<h1>`/subtitle used Tailwind utilities (`text-3xl font-bold
   text-gray-900` / `text-gray-500 mt-1`) instead of the inline-style pattern
   every other page uses (`fontSize: 22, fontWeight: 600, color: "#111827",
   letterSpacing: "-0.02em"` + a 13px `#6B7280` subtitle) — Reports, Defects,
   SOW, Dashboard, Projects all agree on this exact style. Vibe Testing's
   heading rendered noticeably larger (30px vs 22px) and heavier (700 vs
   600) with no letter-spacing, reading as a different design language for
   the same kind of element. Both headings (Results/Coverage/Skills view and
   New Test view) now match byte-for-byte.

Verified live via a temporary unauthenticated route (deleted after): computed
`h1` style resolves to exactly `22px / 600 / rgb(17,24,39) / -0.44px`
letter-spacing (= `-0.02em`), and `body`'s grid+glow `background-image` reads
through unobstructed with no wrapper class re-covering it.

**2026-08-08 — `window.confirm()` replaced app-wide with an animated global
confirm dialog.** Approved from a live, clickable preview before any code
changed. `lib/confirm.ts` exports `confirmDialog(options): Promise<boolean>`
— a drop-in replacement for `window.confirm()`'s return value via a
module-level listener, not a hook, so it's callable from any client function
without React's hooks-in-components constraint (mirrors how `toastSuccess()`
already works). `ConfirmDialogHost` (`components/ui/confirm-dialog.tsx`) is
mounted once in `Providers.jsx`, next to `<Toaster />`, and portals into
`document.body` so it always renders above the sidebar and every page
regardless of ancestor stacking contexts. All 7 existing
`window.confirm(...)` call sites now call `confirmDialog()` instead —
[reports/page.jsx](frontend/src/app/reports/page.jsx),
[ResultsTab.tsx](frontend/src/components/ai-testing/ResultsTab.tsx),
[SkillsTab.tsx](frontend/src/components/ai-testing/SkillsTab.tsx) (×3 —
delete one, bulk-delete, bulk-run), [SowCheckpointsSection.tsx](frontend/src/components/SowCheckpointsSection.tsx),
and [sow/[id]/page.jsx](frontend/src/app/sow/%5Bid%5D/page.jsx) — with a
`tone` of `"danger"` (destructive: delete a run/skill/SOW source) or
`"neutral"` (a pause that isn't data loss: bulk-running skills, regenerating
a SOW over hand-edited sections).

Same card-over-dimmed-backdrop shape the app's existing SOW/Defects delete
modals already use, now shared and properly animated: 220ms scale+fade entry
on `--ease-out` (the one easing curve already used everywhere in this app),
a quicker 160ms fade-only exit so leaving reads as dismissing rather than
rewinding — the same asymmetric-duration logic already on the ink button's
hover crossfade. The two buttons inside are the platform's real shared
`Button` component (`outline` for Cancel, `destructive` or `invert` for
Confirm depending on tone) — no bespoke button CSS lives in the dialog,
verified live by checking each rendered button actually carries the real
variant's classes and rim slot, not a lookalike.

The icon is a genuinely animated glyph, not just a static icon riding the
card's fade: every outline/mark path carries SVG2 `pathLength="1"` so a
plain `stroke-dasharray`/`stroke-dashoffset` CSS transition draws it in
(120ms after the card settles, so it reads as the entrance's last beat, not
a second unrelated animation), and its dot fades in a further beat later.
Destructive tone alone also gets a soft looping pulse ring behind the icon —
the one place continuous motion earns its keep, since it's an irreversible
action awaiting a decision; the neutral tone gets the entrance draw only, no
loop, so a pulse doesn't turn into decoration repeated on every dialog in the
app. `prefers-reduced-motion: reduce` collapses the card to an instant
opacity-only crossfade, the icon to fully drawn with no transition, and kills
the pulse ring outright — every override is re-stated at the same selector
specificity as the animated rules it replaces rather than a flatter
`.confirm-card` guess, since the entered-state selectors are more specific
and would otherwise silently win mid-transition.

Verified live: mounted a temporary unauthenticated route (deleted after)
since every real call site is behind auth. Confirmed both promise-resolution
paths (Cancel → `false`, Confirm → `true`), Esc-to-cancel, backdrop-click-to-
cancel, click-inside-the-card does *not* dismiss it, the compiled CSS matches
the approved design exactly byte-for-byte, and each rendered button carries
its real variant's classes (`bg-foreground`/`text-background` for the ink
Continue button, the rim slot + `btn-white` family for Cancel and for the
destructive Delete button). The Browser pane's document was hidden for this
session, which pauses Chrome's compositor clock entirely (`getComputedStyle`
froze even on an inline `!important` write) — animation *playback* couldn't
be screenshotted, but every structural and functional path was confirmed
through DOM/class assertions instead.

**2026-08-08 — App-wide canvas texture: grid + bottom glow, replacing flat
white/black.** Approved from a live preview before any code changed (grid
density, glow color and both themes were signed off first). Implemented as one
`body` rule in `app/global.css` — a faint 34px grid plus a radial-gradient
bloom pinned to the bottom of the viewport via `background-attachment: fixed`
on all three layers, so it reads as one stationary canvas behind login,
loading and scrolled content alike rather than repeating or drifting with
scroll. Built entirely on existing tokens (`--background`, `--foreground` for
the grid lines, `--mascot-accent` for the glow — the same warm orange already
used on button rims), via a new `--bg-grid-line` custom property that `.dark`
overrides once; no image asset, so it holds at any width or aspect ratio and
the not-yet-built dark-mode toggle will pick it up automatically the moment
`.dark` is applied. `AppShell`'s outer flex container changed from an opaque
`var(--muted)` fill to `transparent` so the texture shows through the
authenticated app's content canvas too, not just the pages outside AppShell —
the sidebar rail keeps its own solid background, unaffected, so nav text stays
on a flat, fully legible surface.

Getting the rule to actually render surfaced a real bug: `app/layout.jsx` had
an unlayered `<style>` tag hardcoding `body { background: #F9FAFB; color:
#111827 }`. Unlayered CSS always wins over `@layer base` regardless of source
order in the document, so that inline tag was silently shadowing
`global.css`'s themed `body` rule — including its existing
`background-color: var(--background)` — on every single page, the whole time,
independent of this feature. Removed the two hardcoded properties (kept
margin/padding/font-family, which nothing else claims); `body` now actually
resolves to the theme.

**2026-08-08 — Option group rolled out platform-wide; defects gained a
recoverable delete.** The segmented row control introduced on `/sow` (below) is
now the shared shape for row actions, and `.btn-option-group` was generalised to
any number of segments: corner rounding is positional (`:first-child` /
`:last-child` / `:only-child`) rather than passed per call site, so a group
whose segments vary by row rounds itself correctly. Those positional rules sit
*unlayered* in `app/global.css` on purpose — `@layer base` loses to Tailwind's
utilities layer no matter the specificity, and they have to beat the shared
Button's `rounded-lg`. Segments are `min-width: 72px` rather than a fixed width,
so a transient label ("Starting…", "Replay") grows instead of clipping. Applied
to: Reports and Vibe Testing → Results (delete alone, so it renders as a 72×28
rounded square — same height, width and radius as the SOW half, replacing the
32px circle), Vibe Testing → Skills (Edit · Run · Delete), and Defects (Edit ·
Start · Delete; its Actions column widened 90px → 216px to hold three segments,
and its buttons moved from `size="xs"` to `size="sm"` because a 24px Edit beside
a 28px Delete is the seam breaking). The free-standing circular delete is
unchanged everywhere it is still used — Users, the Skills bulk toolbar, the SOW
detail page — and no hover choreography was touched anywhere.

Defects had no delete at all: the Next proxy forwarded `DELETE /api/defects/:id`
to a FastAPI route that did not exist. It now does, as a **soft** delete
(migration `0044`), matching the SOW document delete — a bug record is the audit
trail for a failure, so removing one hides it rather than erasing it. `defects`
gains `deleted_at` and `deleted_by` (FK `users` `ON DELETE SET NULL`, so
removing a user never takes bug history with it) plus a partial index on
`deleted_at IS NOT NULL`, which is the selective side; every ordinary read adds
`deleted_at IS NULL` and matches essentially the whole table. `DELETE` is gated
on the same `defects` permission as editing — it is a reversible hide, not an
erase — while the new `POST /api/v1/defects/{id}/restore` and the `?deleted=true`
recovery listing are admin/QA-lead only, since undoing someone else's delete is
supervisory. Both write single-statement `UPDATE … WHERE deleted_at IS [NOT]
NULL … RETURNING`, so a double-submit 404s instead of silently re-stamping a
different user as the deleter. `PATCH` now looks up `AND deleted_at IS NULL`: a
deleted defect 404s on every write path exactly as a hard-deleted one would.
In the UI a "Deleted" filter chip (visible only to the roles that can act on it)
switches the list to the recovery view, where each row reads "Deleted by
&lt;name&gt; · &lt;date&gt;" under its title and offers Restore in place of
Edit/Start/Delete. It is a separate axis from the status chips, not another
status value — a deleted defect still has a status, and conflating them would
make "Deleted + Open" unexpressible.

**2026-08-08 — SOW library row actions joined into one option group.** Rename
and Delete on `/sow` were two loose controls with 12px of air between them, so
a row's two options read as unrelated. They are now a single segmented pill:
two equal 72×28 halves sharing one 8px outer radius and a flush seam, built
from the existing shared parts rather than a third button design — the Rename
half is the standard white `Button` (`variant="outline"`, `size="sm"`) with its
right radius squared off, and the Delete half is the same `DeleteIconButton`
whose hover reveal ships everywhere else. A new opt-in `.btn-option-group`
class in `app/global.css` supplies the only differences: the delete button
rests at full width and anchors left (in a group there is no empty room to
expand into — the space to its left *is* the Rename half), squares its left
corners, and lifts 2px on hover so the half under the pointer detaches from
the seam. Icon and label offsets are re-derived for the 28px height, and the
disabled rules are restated inside the group scope because they tie the group's
hover rules on specificity and would otherwise lose on source order. Scoped to
the SOW library only: Reports, Users, Skills, Results and the SOW detail page
keep the free-standing circular delete button unchanged.

**2026-08-08 — TDD extraction surfaced in the SOW tab.** The v2 extractor
(`backend/app/services/tdd_extraction.py`, migration `0043`, spec in
`TDD_EXTRACTION_SPEC.md`) already classified every checkpoint by test type,
behaviour category and grounding, and recorded what its testability gate
excluded — but none of it reached the API or the UI, so a SOW's negative and
edge coverage was invisible and the gate was unauditable from the app. `GET
/api/v1/visual-audits/sow/{id}` now returns `test_type`, `category`,
`grounding`, `behaviour_key`, `priority` and `coverage_gap` on each
checkpoint, and `excluded_zones` + `coverage` on each part. The SOW
Checkpoints panel renders a coloured test-type badge per checkpoint (negative
red, edge amber — a negative checkpoint PASSES when the system refuses, so it
can never share a visual register with a happy path), a note under any
`derived` checkpoint saying the expectation came from standard QA practice
rather than the document (so a failure is triaged as a possible spec gap
first), a per-document coverage roll-up whose headline is the
negative+edge ratio against the spec's 0.40 acceptance gate, and a collapsed
audit list of the sections the gate skipped as non-testable with the reason
for each — shown rather than hidden, because a filter nobody can see is a
filter nobody can audit. The Skills tab carries the same negative/edge badge,
which is where it matters most operationally: a red replay result on a
negative skill means the product *accepted* something it should have refused,
the opposite reading from a red positive skill. All fields are optional
throughout; skills and parts produced before `0043` render exactly as they did
before rather than being defaulted to a guessed classification, and
re-analysing the artifact is the migration path.

The same threshold is now also enforced in the worker: after a part is
analysed, `ratio_gate_warning()` logs a `WARNING` naming the artifact, the
part and the actual counts whenever negative+edge coverage lands below 40%.
This is a *quality* gate, distinct from the existing error path — it catches
extraction succeeding while producing the wrong shape of output, which is
otherwise only discoverable by reading every generated skill by hand, and is
exactly how the original happy-path-only defect survived undetected. It never
fails the parse or discards checkpoints. It stays silent on parts with fewer
than four checkpoints (the ratio is statistically meaningless at that size and
a warning nobody can act on trains everyone to ignore the warning) and on
parts with none at all (a pricing or timeline section correctly yields
nothing — that is the testability gate working, not extraction drifting).

**2026-08-09 — Live extraction status: the steps that actually ran.** Clicking
Extract Skills/TDDs previously produced one sentence and then silence for
minutes while a multi-part document worked through the pipeline. The obvious
fix — a fixed list of phases in the UI, ticked off by inspecting SowPart rows —
would have shown the same four steps in the same order regardless of what
happened: claiming "identifying feature sections" on a run with `TDD_ZONING=0`,
and staying silent on gap repair, the variant cap and the cross-part merge,
which are the stages most worth knowing fired. PRODUCT.md's first design
principle is that copy must never claim progress that isn't happening, so the
events are written by the code doing the work (`app/services/sow_progress.py`,
new `sow_ingest_events` table, migration `0046`) and the panel renders whatever
it finds, in order, with no step list of its own. A stage that did nothing
emits nothing; a stage that was skipped says *skipped* and draws a dash rather
than a green tick, because "the repair pass found nothing to repair" and "the
repair pass never ran" are different facts. The engine stays database-free —
it takes an `on_progress` callback rather than a session, so its whole test
suite still runs with no database, and a callback that throws cannot break the
extraction it is reporting on. Events are emitted on their **own** session, the
load-bearing detail: the worker holds one transaction open for an entire part,
so an event written on it would only become visible once the work it describes
had already finished. That also means the timeline survives a failed part,
which is the run most worth looking at. Zoning reports itself rather than
letting the caller infer from the return value — when the 85% safety valve
discards an over-aggressive verdict it returns `excluded=[]`, indistinguishable
from "nothing needed excluding", and only that function knows which happened.
`GET /api/v1/visual-audits/sow/{id}/progress?after=N` serves the timeline
incrementally so a poll during a long ingest returns the new rows rather than
the whole history every two seconds.

**2026-08-09 — The naming reference is visible on the project page.** The UI
naming reference existed only in a database row, an API endpoint and a worker
log line, which meant its failure mode was silent: if the vision pass misread
the UI or managed two screens out of twelve, extraction quietly fell back to
the document's wording and the cost surfaced weeks later as a failed run that
looked like a product bug. `ProjectUiInventoryPanel` sits on the project detail
page under Environments and shows screens, label count, build date, and — on
expand — the labels themselves, grouped by screen. Read-only with no rebuild
button on purpose: the reference rebuilds itself when the project's evidence
changes, so a manual rebuild would only re-run a call that is already current,
and when it is wrong the fix is better evidence rather than another build. A
reference older than 90 days gets an age badge, since a confidently wrong label
is worse than a missing one — the test looks grounded and its failure reads as
a defect — though age alone cannot prove staleness, so it warns rather than
invalidates. Feature-detected like the other Vibe Testing surfaces: the
endpoint 404s when the flag is off and the panel renders nothing rather than an
error box for a deliberately disabled feature.

**2026-08-08 — The UI naming reference: tests that name real buttons.**
Extraction only ever read the requirements document, so a checkpoint said
"click Submit Application" because that is what the document called it, while
the product's button reads "Apply Now". The test then failed for a reason that
was neither a product defect nor a spec gap — the most demoralising red result
there is, because it looks like a bug and isn't. New
`app/services/ui_inventory.py` (migration `0045`, opt-out via
`TDD_UI_INVENTORY=0`) runs one vision call per *project* over the evidence
uploaded with the SOW — screenshots, plus control and field names already
recovered from digested walkthrough videos, which is text the video digest
already paid for — and records what each screen, button, field and nav item is
actually called. That naming reference is stored on `project_ui_inventory` and
appended to both the extraction and the gap-repair prompts, so a repaired
negative case names real controls too. Deliberately not live navigation: driving
the real product would ground the same labels at per-test cost and would need
working credentials and a deployed environment at extraction time, whereas today
a SOW for a product that does not exist yet still extracts — a property worth
keeping. The hard rule is that the inventory is **vocabulary, not
requirements**: a button visible in a screenshot is not evidence anyone asked
for it to be tested, and an inventory that could introduce behaviours would
reintroduce the original "everything becomes a TDD" defect from the opposite
direction, so that rule is stated in the vision prompt, restated at the point of
use, and pinned by a test. The prompt also says what to do when a control is
*absent* from the reference — use the document's wording, don't conclude the
control is missing — because the reference is partial by nature, and it demands
transcription rather than paraphrase, since a wrong label looks authoritative
while a missing one merely falls back to the document. Staleness is keyed on
which evidence *existed* at build time rather than which the build managed to
use: keying on "used" would leave the stored set permanently unequal to the
current one whenever a screenshot was skipped as oversized or a video was still
digesting, and every part of every SOW would rebuild and pay for another vision
call. Every failure path — no project, no evidence, an unreadable file, a failed
vision call — returns nothing and extraction proceeds on document text exactly
as before; a vision call cannot fail a SOW ingest.
`GET /api/v1/visual-audits/projects/{id}/ui-inventory` exposes what was read.

**2026-08-08 — TDD_DERIVED_AS_SKILLS now applies to videos too.** The flag is
documented as keeping derived negative/edge checkpoints out of the Skills
table, and the SOW worker honoured it — the video worker never checked it at
all, so with the flag off, derived checkpoints from a walkthrough still became
skills. The flag defaults on, so nothing was broken in practice, but the flag
described the Skills *table* while only governing one of the two sources
feeding it: the table would look filtered while half of it was not, and nothing
in the UI said which half. That is worse than having no flag. The video worker
now applies the same check and logs each held checkpoint by name, and a new
test asserts the two workers produce the same outcome from the same input —
the property that actually matters is one flag, one result, regardless of
which source produced the checkpoint.

**2026-08-08 — Walkthrough videos get the same coverage backstop as SOWs.**
The video path categorised what a recording showed and derived the negative and
edge cases each category requires, but it never ran the code-side checks the
SOW path runs afterwards: Stages 4, 4b and 4c were silently document-only. So a
walkthrough-derived behaviour missing a variant its category demands was
neither flagged nor re-requested, and a verbose one was unbounded — two sources
feeding one Skills table at two different levels of rigour, which is exactly the
kind of difference nobody remembers when reading the results.
`apply_variant_backstop()` applies the coverage check and the variant cap to an
already-flat checkpoint list by grouping on `behaviour_key` (the SOW path
applies both per behaviour as it builds them, having the behaviours in hand),
and `classify_and_expand` now runs it followed by `repair_coverage_gaps`. The
expansion prompt *asks* for the required variants; this checks in code that
they actually arrived rather than trusting the model's claim. Checkpoints with
no behaviour key — visual ones, anything from the legacy path — pass through
untouched, having no category contract to enforce, and a behaviour's variants
are re-emitted at the position of its first member so they stay adjacent and in
order. Enrichment still never raises: a video that digested successfully is not
failed by it, now across two provider calls rather than one.

**2026-08-08 — Derived-failure report: inferred expectations that never hold.**
Most negative and edge tests are `grounding="derived"` — the source document
does not enumerate its own failure modes, so the expectation was inferred from
standard QA practice. That means a failing derived test has two possible
explanations needing opposite responses: the product is wrong (raise a defect),
or the *inference* is wrong because the product deliberately behaves
differently and the document never said so (fix the document). Nothing
aggregated that, so in practice every failure was triaged as the first, and the
second never surfaced at all. New `GET /api/ai-testing/spec-gaps` lists derived
skills that have **never once passed** across at least `min_runs` (default 2)
decided runs, rendered as a "Possible spec gaps" panel at the top of the AI
Testing → Coverage tab. The selection rule is "never passed" rather than "fails
often" on purpose: a derived test that sometimes passes is flaky,
environment-dependent or data-dependent, which are product and infrastructure
concerns with different owners, while one that has never passed is a systematic
disagreement between the inferred expectation and the product — and requiring
consistency is also what keeps the list short enough that someone actually
reads it. Undecided runs (needs_review, inconclusive, cancelled, pending,
running) are excluded from both the numerator and the denominator, since
treating "we don't know" as "it failed" would manufacture spec gaps out of
infrastructure problems. The response carries its own denominators
(`total_derived_skills`, `evaluated_skills`) so "7 gaps" can be read honestly
against 12 inferred expectations or against 400, and the panel says nothing at
all rather than claiming a clean bill of health when nothing had enough runs to
judge. The rule lives in `is_spec_gap_candidate()`, separated from the endpoint
so it is unit-testable without a database. It produces candidates, not a
verdict — a never-passing derived test can still be a real long-standing
defect; the point is that it can no longer only be read that way.

**2026-08-08 — Variant volume is capped by priority, not by accident.** A rich
behaviour legitimately produces many variants — an input-validation rule over
several fields has a distinct negative case per field, and all of them are real
tests — and nothing bounded that. The only thing standing between a verbose
behaviour and an unbounded skill list was the extraction call's `max_tokens`,
which truncates the model's JSON at whatever character it happens to reach: the
tests you lose are chosen by accident, the loss is invisible, and it lands in
the middle of an array so it can take a well-formed checkpoint with it. New
Stage 4c (`cap_variants()`, opt-out via `TDD_VARIANT_CAP=0`) replaces that
accident with a decision. The ceiling is per *behaviour* (8), not per part or
document — a document with fifty modest behaviours is fine and must not be
trimmed, while one behaviour with twenty variants is where the runaway actually
happens. Selection keeps one checkpoint of every test type present *before*
anything else, since dropping the only edge case to keep an eighth negative
would gut the negative+edge ratio and remove a category's required coverage;
then fills the remaining slots by priority (smoke > sanity > regression), ties
broken on document order so re-analysing a part gives the same answer; then
restores document order for the survivors, because selection is by priority but
presentation should not be. It runs after the coverage check and before gap
repair — capping first would drop a required variant, get it flagged as a gap,
have repair re-request it and drop it again, a loop that spends tokens forever
and converges on nothing. Nothing is dropped silently: the discarded tests are
named in a `WARNING`, the surviving checkpoint carries `capped_variants`, the
scorecard sums it, and the SOW tab shows both.

**2026-08-08 — Cross-part reconciliation: one feature, one set of skills.** A
SOW almost always describes a feature twice — once in a summary or scope
section, once in detail — and because extraction runs per chunk and dedupe ran
inside it, those landed in different parts, got different behaviour names, and
produced two near-identical sets of checkpoints and two near-identical sets of
Skills. No per-part stage could see it. New Stage 6
(`reconcile_across_parts()`, opt-out via `TDD_RECONCILE=0`) runs at the
document level in `_merge_checkpoints`, and splits the job across two
deliberately different mechanisms. Merging — "is this the same test?" — is
decided in Python by string similarity on the objective; it is the step that
must not be wrong, so it does not get to be creative, and because it is
deterministic and free it runs on every part completion, which is what stops a
duplicate Skill being *created* rather than cleaning one up afterwards. It
deliberately does not require the behaviour names to match, since two parts
naming one behaviour differently is exactly the case that has to merge. Naming
— mapping near-duplicate behaviour keys onto a canonical one so a behaviour's
variants group together — is a judgement call and goes to an LLM, restricted to
keys actually sent, failing open to identity, and running once when the last
part lands so a 12-part document pays for one call rather than twelve. The
threshold is set high (0.90) and guarded three ways — never across test types,
never within a single part, first occurrence wins — because the costs are not
symmetric: failing to merge leaves a duplicate someone deletes, while wrongly
merging silently deletes a test, and the thing that would have reported it is
the test that no longer exists. Nothing is lost: the survivor carries
`merged_from_parts`, which the API returns and the SOW tab renders as "also
stated in part 4", and each part's own stored checkpoints are left untouched as
the record of what that section actually produced.

**2026-08-08 — Coverage gaps are repaired, not just reported.** The extractor
already checked in code whether each behaviour had the test types its category
requires, and flagged the ones that didn't — then shipped the hole anyway. New
Stage 4b (`repair_coverage_gaps()`, opt-out via `TDD_GAP_REPAIR=0`) re-asks for
*only* the missing variants, supplying the behaviour's category, its required
probes and its already-written happy path so the new test reuses real screen
and field labels instead of inventing them. It runs before dedupe, so a repair
that restates an existing case collapses under the same rule as everything
else. Three properties make it safe to have: coverage is recomputed from the
repaired checkpoints rather than from the model's reply, so a variant that came
back invalid or was quietly skipped leaves the gap flag standing (reduced to
what is still missing) — the stage does not grade its own work; every failure
path returns the checkpoints untouched, because losing a part's real
requirements to a failed enrichment call is far worse than shipping the flagged
gap; and everything it produces is `grounding="derived"` regardless of what the
model claims, since a repaired variant is reasoned from QA practice rather than
read out of the document. A `behaviour_key` nobody asked about and a
`test_type` that wasn't missing are both dropped — silently widening the output
is exactly how the original "everything becomes a TDD" defect behaved. Capped
at 12 behaviours per part, with the skipped ones named in a `WARNING`, because
a part with more gaps than that has a systemic problem another call won't fix.

**2026-08-07 — Login form spacing; rocker-switch password reveal.** The login
form's email and password inputs now sit in bordered wells (44px tall, 14px of
inner padding, 12px apart) instead of floating as bare transparent inputs, and
focus is expressed once by the well via `focus-within` rather than a ring that
only ever appeared on one of the two rows. `PasswordRevealSwitch` — the single
show/hide control shared by the login form and the AI Testing credential
dialog, so this changes both — is now a physical rocker switch: neutral grey
while the password is masked, green while it is visible. Masked is the resting
state, so colour appears only for the state that is actually happening; a
permanently red control on a login screen reads as an error when nothing is
wrong. It is still a real
`<input type="checkbox">` inside a `<label>` (tab order, Space, screen-reader
semantics unchanged), and the supplied styled-components snippet was ported to
a scoped stylesheet rather than adding a CSS-in-JS runtime. Fixed base size of
13.333px puts it at exactly 44px tall — the minimum touch target and a match
for the field height beside it. The snippet's 1px focus glow was replaced with
a real `:focus-visible` ring (it was invisible against the switch's own green),
and the animation is dropped under `prefers-reduced-motion`, where the colour
alone carries the state. On hover it shows the shared Base UI tooltip (the same
dark pill, arrow, and fade/zoom in-out used by Skills' "Test setup" button)
carrying mascot copy that tracks the switch state: "Let the spider peek" while
masked, "Back in the web" while revealed. The tooltip picks its own colours from the surface it
sits on: it is portalled to `<body>`, so it cannot inherit anything from the
card it visually belongs to, and the shared dark pill was near-black on the
near-black login card. `surfaceIsDark` walks up from the switch compositing
background colours until they reach opacity, then inverts the pill (white on
the login card, unchanged dark on the white credential dialog). Compositing
rather than first-background-wins is load-bearing: the login wells are
`bg-white/[0.04]` over `bg-gray-950`, so a naive read returns white and picks
the wrong tooltip. The tooltip is decoration, not the
accessible name — that stays on the input's `aria-label`, and the adjacent
Show/Hide text still carries the state for keyboard users, since Base UI binds
its focus trigger to the trigger element and focus here lands on the nested
checkbox. The Radix `Switch` primitive it previously wrapped is
now unused by any caller.

**2026-08-07 — One global loading overlay; always-visible scrollbar.** Loading
is now a single fullscreen overlay owned by `NavigationLoadingProvider`
(mounted in `Providers`, outside `AppShell`, so it covers the sidebar as well
as the content). It turns on synchronously from a capture-phase click on any
internal link — before the router starts — so no chrome or page skeleton can
paint ahead of it, and a `popstate` listener covers back/forward. Pages no
longer render a loader themselves: they call `usePageLoading(isLoading)`, and
the overlay stays up until the destination's data lands. The in-page
`<GlobalLoader fullscreen={false} />` variant is removed entirely (it was what
produced the boxed loader inside the content area), and `GlobalLoader`'s 90ms
content delay is now 0. Separately, the main content area moves from
`overflow-y: auto` to a styled, always-present `overflow-y: scroll` with
`scrollbar-gutter: stable`, so the scroll position is visible at rest and
layout no longer shifts sideways between long and short pages.

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
