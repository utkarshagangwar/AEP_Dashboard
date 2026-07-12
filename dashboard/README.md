# AEP — Automation Execution Platform

Internal QA platform combining manual test-suite management with AI-driven
testing: goal-based browser agents, requirements/video-to-checkpoint
extraction, and visual regression auditing.

> **Maintenance rule (in effect):** this file must be updated after every
> change that affects functionality, architecture, or data flow — not just
> at release time. Treat it as the single source of truth for "what does
> this platform actually do and how is it built right now." When you land a
> change, add/update the relevant section below and add a dated entry to
> [Changelog](#changelog).

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 18, Tailwind, shadcn/ui-style components |
| Backend API | FastAPI 0.115, SQLAlchemy 2.0, Alembic migrations |
| Background jobs | Celery 5.4 (+ embedded Celery Beat on the worker) over Redis |
| Database | PostgreSQL (external/managed — `DATABASE_URL`, not containerized locally) |
| Queue/broker | Redis 7 (containerized) |
| Browser automation | Playwright 1.49, `browser-use` 0.1.45 (goal-based AI agent) |
| AI providers | Google Gemini (`langchain-google-genai`, direct REST for video), OpenAI, Anthropic, OpenRouter — routed per task |
| Reverse proxy | Nginx (TLS termination, routes to frontend/backend) |

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
       │  (Next.js)  │───────────▶│  (FastAPI)   │
       │  :3000      │  proxy     │  :8000       │
       └─────────────┘            └──────┬───────┘
                                          │ enqueues jobs
                                          ▼
                                   ┌──────────────┐        ┌─────────┐
                                   │ celery_worker│◀──────▶│  redis  │
                                   │ (+ beat)     │ broker/ └─────────┘
                                   └──────┬───────┘ backend
                                          │
                     ┌────────────────────┼─────────────────────┐
                     ▼                    ▼                     ▼
              PostgreSQL (external)  visual_qa_data       AI providers
              (all app state)        (shared volume:      (Gemini/OpenAI/
                                      uploaded SOWs,        Anthropic/
                                      videos, screenshots,  OpenRouter)
                                      diffs)
```

The frontend never talks to Celery or Postgres directly — every mutation
goes through the FastAPI backend, which writes to Postgres and (for
long-running work) enqueues a Celery task and returns immediately. The
worker and the API share the `visual_qa_data` volume so uploads made via
the API are visible to the worker and vice versa.

## Folder structure

```
dashboard/
├── backend/
│   ├── app/
│   │   ├── api/v1/         # route files, one per resource (see below)
│   │   ├── core/           # config, security, db session, seed, dependencies
│   │   ├── models/         # SQLAlchemy ORM models
│   │   ├── schemas/        # Pydantic request/response schemas
│   │   ├── services/       # business logic (LLM routing, ingestion, etc.)
│   │   └── workers/
│   │       ├── celery_app.py
│   │       └── tasks/      # one module per Celery task family
│   └── alembic/versions/   # migrations, sequential 0001..NNNN
├── frontend/
│   └── src/
│       ├── app/             # Next.js App Router pages
│       ├── components/      # shared React components
│       └── utils/           # apiClient, auth store, etc.
├── docker/                  # Dockerfiles for backend/frontend/nginx
└── docker-compose.yml
```

## Feature map

Navigation (left sidebar) → what each section does:

| Nav item | Route | Purpose |
|---|---|---|
| Dashboard | `/dashboard` | Stats overview, open to any logged-in user |
| Projects | `/projects` | Project/environment/credential-profile management |
| Defects | `/defects` | Defect tracking |
| Execute | `/execute` | Manual/deterministic test suite execution (Robot-style suites) |
| Reports | `/reports` | Run history and result reporting |
| **Vibe Testing** | `/ai-testing` | AI testing suite — see below |
| Admin → Users | `/admin/users` | User & role/permission management |
| Admin → Audit Logs | `/admin/audit-logs` | Action audit trail |

### Vibe Testing (`/ai-testing`) — the AI testing suite

Three tabs:

- **New** — goal-based AI test run (type a natural-language goal, a
  `browser-use` agent plans and executes it), plus three ingestion panels
  feeding that goal box:
  - **SOW Checkpoints** — upload a requirements document (`.txt`/`.md`/`.pdf`).
    The Brain (Gemini/LLM router, `design_ingest.py`) extracts structured
    QA checkpoints. Large documents are chunked into parts
    (`sow_parts` table) and analyzed one part at a time on demand.
  - **Video Walkthrough** — upload a screen-recording (`.mp4`/`.webm`/`.mov`).
    Gemini's Files API watches the video and extracts checkpoints the same
    way. **Requires a declared platform/product name** (see
    [Changelog](#changelog) — 2026-07-12) so the model has something to
    check on-screen content against instead of guessing.
  - **Visual Audit** — pixel-diff + AI vision comparison of a live page
    against a reference design (Figma export or uploaded PNG).
  - **Figma Import** — pull frames directly from a Figma file as reference
    designs.
  - Every functional checkpoint extracted from a SOW or video is saved
    straight to the **Skills** tab as a runnable prompt skill — no live
    browser run needed to produce it.
- **Results** — history of past AI test runs (summary, step-by-step replay,
  screenshots).
- **Skills** — reusable skills, either recorded (browser action replay from
  a passed run) or prompt-only (from SOW/video extraction). Editable by
  hand; manual edits are protected from being overwritten by re-analysis.
  Sortable by name or date added (asc/desc); per-row checkboxes support
  bulk delete, bulk project assignment, and bulk run.

### Visual QA / "Memory Bank" pattern (`app/models/visual_qa.py`)

All Visual QA source material (Figma PNGs, SOW documents, walkthrough
videos) lives in one `design_artifacts` table, deduplicated by SHA-256 so
identical content is never re-analyzed (or re-billed) twice. Parsed output
lands in `design_rules` (one row per artifact, JSONB checkpoints). Feature-
flagged behind `VISUAL_AUDIT_ENABLED` — every endpoint 404s when it's off.

Key tables: `design_artifacts`, `sow_parts`, `design_rules`, `visual_runs`,
`visual_findings`.

### AI test runs (`app/models/ai_runs.py`)

`ai_test_runs` / `ai_run_events` record goal-based agent runs.
`ai_skills` stores both recorded-replay skills and prompt-only skills
extracted from SOW/video ingestion, unified under one upsert path
(`app/services/skill_store.py`) keyed by `goal_hash` / `source_key`.

## Running locally

```bash
docker compose build
docker compose up -d
docker exec <backend-container> alembic upgrade head
```

Required env vars (`.env`, see `backend/.env.example`): `DATABASE_URL`,
`JWT_SECRET_KEY`, `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD` (seeded on
first boot if no users exist), plus whichever AI provider keys the
features you're using need (`GEMINI_API_KEY`/`GOOGLE_API_KEY` for Visual
QA and Video Walkthrough, `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/
`OPENROUTER_API_KEY` as configured).

The backend and celery_worker images are **not** source-bind-mounted —
after editing backend code you must `docker compose build backend
celery_worker && docker compose up -d backend celery_worker` for changes
to take effect. Same for `frontend`.

## Changelog

### 2026-07-12 — Video Walkthrough: mandatory platform name + anti-hallucination guard

**Problem reported:** the Video Walkthrough feature (Vibe Testing → New tab)
was extracting checkpoints that matched an uploaded SOW's content instead of
the actual video.

**Root cause (confirmed by extracting real frames from the uploaded video):**
not a code-level linking bug — SOW and Video pipelines are and always were
fully isolated (separate `artifact_type`, storage directories, Celery
tasks, Gemini call paths). The specific video that had been uploaded was
itself a screen recording of this same AEP dashboard's SOW Checkpoints
panel, with the SOW's text genuinely visible on screen — Gemini was
correctly reading what was in the video; the video was simply the wrong
file for the intent behind the report.

**Fix implemented** (`backend/app/services/video_ingest.py`,
`backend/app/api/v1/visual_audit.py`, `backend/app/models/visual_qa.py`,
`backend/alembic/versions/0025_add_video_platform_name.py`,
`frontend/src/components/SowCheckpointsSection.tsx`):

- `design_artifacts.platform_name` (new nullable column) — the product this
  video is declared to walk through.
- `POST /api/v1/visual-audits/video` now requires `platform_name` as a form
  field (422 if missing, 400 if blank/whitespace-only).
- The Gemini video-digest prompt (`_build_video_prompt`) is now built
  per-upload, anchored to the declared platform name, with an explicit
  grounding rule (only report what's literally on-screen/audible, ignore
  any "recognized" background knowledge of the product) and a **mismatch
  check**: if the on-screen content is clearly a different product than
  declared, the model returns exactly one fully-populated "Video/platform
  mismatch" checkpoint (with `objective`/`instructions`/`notes` explaining
  what it actually saw) instead of silently extracting checkpoints from the
  wrong content.
  - First version of this mismatch checkpoint only filled `notes`, which
    failed `_validate_checkpoint`'s requirement that functional checkpoints
    have non-empty `objective`/`instructions` — the checkpoint was silently
    dropped, surfacing as "No testable requirements found in this video."
    Fixed by requiring the prompt to fill all three fields.
- Frontend: required "Platform / product name" input added to the Video
  Walkthrough uploader only (SOW uploader unchanged); upload button is
  disabled until it's filled in. Declared name is shown next to the file
  name in the list and returned in the API response (`SowOut.platform_name`).

**Verified:** `422`/`400` on missing/blank `platform_name` via `TestClient`;
`202` + persisted `platform_name` on a valid upload; re-ran ingestion on the
actual reported video with `platform_name="AEP"` — it now correctly returns
a single "Video/platform mismatch" checkpoint identifying the on-screen
content as the dashboard's own "Vibe Testing" UI, instead of silently
returning SOW-shaped checkpoints or an empty result.

### 2026-07-12 (follow-up, same day) — mismatch verdict was itself wrong; hard-gated it and fixed model precision

Two problems surfaced in the fix above, both from user testing against the
real video:

1. **The "mismatch" checkpoint was still a checkpoint** — it got saved to
   the `ai_skills` table like any other functional checkpoint (via
   `_save_functional_skills`), i.e. a "this video doesn't match" notice was
   runnable as a skill. Wrong: a failed/ambiguous analysis must never look
   like a normal finding.
2. **The mismatch verdict was factually wrong.** The uploaded video's
   top-left sidebar clearly reads "AEP / QA Platform" for its entire
   duration (confirmed by extracting real frames — large, high-contrast,
   unmissable to a human). With `platform_name="AEP"` declared, Gemini's
   video understanding still concluded `platform_match: false` twice in a
   row — once at default settings, once again after raising
   `generationConfig.mediaResolution` to `MEDIA_RESOLUTION_HIGH` (280
   tokens/frame vs the ~70 default; the officially documented setting for
   reading small on-screen text). Both attempts prove this isn't a
   pixel-resolution problem — it's an *attention* problem: native video
   understanding under-weights small, persistent UI chrome (a header/
   sidebar logo) in favor of the "main content" narrative, even when told
   explicitly to check corners/logos/headers first.

**Fix (`backend/app/services/video_ingest.py`, `docker/Dockerfile.backend`):**

- Response schema restructured: `platform_match` (bool) and `brand_evidence`
  (list of every on-screen/audio brand mention the model found, required
  before it's allowed to judge) are now **top-level** fields, no longer a
  fake checkpoint. `digest_video()` treats `platform_match: false` as a hard
  `IngestError` — `parse_status='error'`, no `DesignRule` row, no skill ever
  saved. Same hard-error treatment for a genuinely empty checkpoint list
  (was previously a silent "done, 0 checkpoints").
- **Still-frame precision assist**: `ffmpeg`/`ffprobe` (added to
  `Dockerfile.backend`) extract 4 plain JPEG stills spread across the
  video's timeline (`_extract_still_frames`, best-effort — never fails
  ingestion if ffmpeg is unavailable). These are attached to the same
  `generateContent` call as ordinary images, explicitly labeled for the
  model to check for branding/text before writing `brand_evidence` — plain
  images are read far more reliably than compressed video frames by the
  same model. This is what actually fixed the false mismatch: re-run with
  stills attached, `brand_evidence` correctly came back
  `["top-left sidebar header reads 'AEP QA Platform'"]` and `platform_match`
  was `true`.

**Verified end-to-end** on the real reported video: `brand_evidence`
correctly identifies the on-screen "AEP QA Platform" branding,
`platform_match: true`, 4 checkpoints extracted and saved. Note the
checkpoints' *content* still describes the SOW text visible in the video
(this particular recording is a scroll through AEP's own SOW Checkpoints
panel, not a walkthrough of a distinct AEP feature) — that's now correctly
grounded, not hallucinated; for a clean demo of this feature, upload a
recording of an actual feature being used, not a document viewer.

### 2026-07-12 — Skills tab: sorting + bulk actions

**Added** (`backend/app/api/v1/ai_runs.py`, `backend/app/schemas/ai_runs.py`,
`frontend/src/components/ai-testing/SkillsTab.tsx`, two new Next.js proxy
routes under `frontend/src/app/api/ai-testing/skills/`):

- `GET /api/v1/ai-testing/skills` gained `sort_by` (`name` | `created_at` |
  `updated_at`, 400 on anything else) and `sort_dir` (`asc` | `desc`).
  `name` sorts case-insensitively (`func.lower`); every sort has `id` as a
  stable secondary key so pagination doesn't reorder rows with tied sort
  values across pages. Default changed from an implicit "most recently
  updated" to explicit `created_at desc` ("date added, newest first") to
  match what users actually asked to sort by.
- Frontend: a single "Sort by" dropdown (Date added newest/oldest, Name
  A→Z/Z→A) next to the existing Project filter.
- Two new bulk endpoints, both `require_permission("vibe_testing")`, one DB
  transaction each, one audit-log entry each (`bulk_delete_ai_skills` /
  `bulk_assign_ai_skills_project`) listing every affected `skill_id`:
  - `POST /skills/bulk-delete` — body `{skill_ids: [UUID]}` (max 200),
    unknown IDs are silently skipped, returns `{deleted: N}`.
  - `POST /skills/bulk-assign-project` — body `{skill_ids: [UUID],
    project_id: UUID|null}`, sets `manually_edited=True` on every row (same
    protection as the single-skill PATCH path), returns `{updated: N}`.
- Frontend: a checkbox per skill row + "select all on this page"; selecting
  any row reveals a bulk action bar (assign-to-project dropdown, Run
  selected, Delete selected, Clear). Bulk run has no dedicated backend
  endpoint — it fires the existing per-skill `POST /skills/{id}/replay` for
  each selected ID via `Promise.allSettled` (each run is independently
  tracked with its own run_id/Celery task, which is the correct shape for
  "run N skills," not something a batch endpoint should collapse) and
  reports a success/failure count instead of jumping into any one run's
  live view. Selection is page-scoped: changing the page, project filter,
  or sort clears it, so a selection can never silently point at rows no
  longer on screen.

**Verified:** `sort_by`/`sort_dir` validation (400 on bad values) and both
sort orders confirmed against real data via `TestClient`; bulk-delete and
bulk-assign-project confirmed end-to-end against a real (throwaway) skill
row — project reassignment persisted with `manually_edited=True`, deletion
persisted (subsequent `GET` 404s), both correctly return a 0-affected count
for nonexistent IDs rather than erroring.
