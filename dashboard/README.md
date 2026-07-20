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
| **SOW** | `/sow` | SOW Creation & Rewrite — see below (Phase 0: document CRUD only so far) |
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

### SOW Creation & Rewrite (`/sow`) — runs the opposite direction from SOW Checkpoints

Full design in `SOW_FEATURE_PLAN.md` at the repo root (including a Hardening
Addendum covering idempotency, concurrency, and the completeness-audit
mechanism the feature's quality guarantee depends on). Short version: where
the existing SOW Checkpoints panel above goes *SOW → checkpoints*, this
feature goes *meeting transcript + meeting recording + design references →
SOW document*, described exhaustively enough (down to individual buttons,
dropdowns, filters, checkboxes, toggles, sliders, three-dot menus) that the
generated SOW can itself be fed straight into that same existing checkpoint
extractor for vibe testing with nothing missing.

**Current status: Phases 0–1 and 3–7 done; Phase 2 (validation-only)
folded into Phase 1's extraction.**
What exists today: document CRUD, source attachment (meeting transcript
paste/upload, meeting recording upload with size/duration caps, design PNG
reference) each independently extracted into a raw requirements ledger,
full document generation — ledger grouped into sections, each section
drafted into typed `content_blocks` and then independently audited for
completeness against its source facts (a coverage score plus a listed
record of anything the audit found missing, not just a hope the drafting
prompt was followed), framed with an LLM-drafted Project Overview/Scope of
Work plus five templated trailing sections, versioned with a genuine
partial-failure model (a `done_with_errors` version still shows every
section that *did* succeed, with failures flagged individually rather than
discarding the whole run) — a structured, per-block-type editor for
hand-fixing any section in place, a client-side diff between versions, and
now export to `.md`/`.docx`/`.pdf` plus a one-click hand-off into the
existing Vibe Testing checkpoint extractor. The `/sow/[id]` page lets you
attach sources, inspect the raw ledger, click Generate, read each
version's sections with their coverage badges and flagged gaps, edit a
section directly, compare two versions, download the current version in
any of three formats, send it to Vibe Testing, and now selectively
rewrite (patch) individual sections — regenerating only the ones you
pick, copying everything else forward unchanged, with hand-edited
sections protected from being silently overwritten unless you explicitly
force it. Full regeneration (fresh Generate) still replaces every
section from scratch and still doesn't preserve hand-edits — that
trade-off is unchanged and the UI still warns before it happens. See the
plan doc's §8 phased delivery table for details on each phase.

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

### 2026-07-20 (latest) — SOW Creation & Rewrite: Phase 7 (rewrite/patch)

**Added:**
- `app/services/sow_patch.py` — `non_patchable_section_keys()` (excludes
  `project-overview`/`scope-of-work` plus the five templated trailing
  sections, since none of them are drafted from one section's own fact
  subset the way functional sections are), `filter_protected_sections()`
  (plan §11.4 — a targeted section that's `edited_by_human` is skipped
  unless its key is also in `override_manual_edits`), and
  `draft_and_audit_section()` — Pass 2 (draft) + Pass 3 (audit) for one
  section, extracted out of `generate_sow_task` so full-generation and
  patch share the exact same drafting/audit logic and can't drift apart.
- `SowRequirementsLedger.assigned_section_key` is now stamped during Pass
  2a grouping in `generate_sow_task` — this is what lets a patch later
  find "which facts belong to this section" without re-running grouping.
- `POST .../documents/{id}/rewrite` — creates a new `kind=patch` version
  (`parent_version_id` pointing at the version it patched), copies every
  non-targeted section forward unchanged in the same transaction, and
  enqueues `patch_sow_task` to redraft only the targeted sections. Rejects
  non-patchable section keys with a 400. Same row-lock concurrency guard
  as `/generate` (plan §11.3) and same rate limit
  (`SOW_GENERATE_RATE_LIMIT`).
- `patch_sow_task` (Celery) — idempotency guard only clears the rows it
  owns (the sections it's about to redraft), never the ones the API
  endpoint already copied forward; redrafts each target section from its
  `assigned_section_key`-matched ledger facts via
  `sow_patch.draft_and_audit_section`; computes aggregate
  done/done_with_errors/error status over the WHOLE resulting version
  (copied + redrafted sections), not just the redrafted ones; carries
  `generated_by_model` forward from the parent version, merged with any
  newly-used models.
- Frontend (`/sow/[id]`): a checkbox picker over the current version's
  patchable sections, an inline "hand-edited — force regenerate anyway?"
  override checkbox that only appears for selected sections that are
  `edited_by_human`, and a "Rewrite N sections" button — reuses the exact
  same job-polling machinery already driving Generate's progress UI,
  since both endpoints create the same kind of job row.
- `generate_document` (`/generate`) retroactively got the
  `@limiter.limit(_generate_rate_limit)` decorator it was supposed to
  have since Phase 3 — caught during this phase's self-review since the
  plan calls for both endpoints to share the rate limit.

**Fixed during implementation (self-review, not shipped broken):**
- Race condition in `/rewrite`: the endpoint originally read/validated
  `target_sections` against `doc.current_version_id`'s sections BEFORE
  acquiring the `SELECT ... FOR UPDATE` row lock. A concurrent `/generate`
  completing in that window could move `current_version_id`, making every
  downstream check (parent sections, missing-key validation, protected-
  section filtering) stale relative to the version actually being locked
  and patched — worst case, a patch could silently apply against a
  superseded parent and then overwrite `current_version_id`, discarding
  the concurrent generate's newer output. Fixed by locking first, then
  reading `current_version_id` and everything derived from it off the
  freshly-locked row.

**Verified in this sandbox:**
- `py_compile` on every changed backend module.
- Full `app.main` import (106 routes registered, no import errors).
- OpenAPI schema regeneration confirms `/rewrite` and `/export` are both
  present with correct methods.
- `TestClient` 401 sweep with no auth header across `/rewrite`,
  `/generate` (re-checked after the retroactive rate-limit decorator),
  `/export`, `/send-to-checkpoints`, `PATCH .../sections/{key}`, and
  `GET .../versions` — all correctly 401 before any route-specific logic
  runs.
- Celery task registration: `sow_generation.patch_sow_task` appears
  alongside `sow_generation.generate_sow_task` in
  `celery_app.tasks` after `import_default_modules()`.
- Frontend JSX syntax for the new rewrite panel verified via a babel
  parse (`@babel/preset-typescript` in isTSX mode +
  `@babel/plugin-syntax-jsx`) — this validates syntax, not runtime
  behavior.
- `draft_and_audit_section()` itself was already function-tested in
  Phase 6/7 development via mocked `llm_router.complete` for both its
  success and drafting-failure paths (real validation logic exercised,
  not just imported).

**Not verified — no live Postgres/Celery worker/Docker container in this
sandbox:**
- The actual `/rewrite` request/response cycle end-to-end.
- `patch_sow_task` executing against a real database (the copy-forward
  transaction, the idempotency-guard delete scoped to only target
  sections, the aggregate status computation over copied + redrafted
  sections together).
- The row-lock race-condition fix under genuine concurrent load (only
  reasoned through, not exercised with two real concurrent requests).
- The frontend rewrite panel rendering and behaving correctly in a
  running Next.js app (only syntax-checked, never rendered).

**Known limitation:** documents that were fully generated before this
phase shipped won't have `assigned_section_key` populated on their
ledger facts (that stamping is new in this phase's `generate_sow_task`
change). `/rewrite` on such a document will find zero facts for every
target section until it's regenerated once via `/generate` after this
deploy. Not fixed with a backfill migration in this phase — flagging it
here rather than leaving it to be discovered by trial and error.

### 2026-07-20 (earlier) — SOW Creation & Rewrite: Phase 6 (export + send-to-checkpoints)

**Added**, on top of Phase 5's editor (see entry below): the two consumption
paths a generated SOW was always meant to support besides the in-app view —
downloading it as a real document, and feeding it straight into the
platform's existing Vibe Testing checkpoint extractor.

- **`app/services/sow_export.py`** (new) — `render_document_markdown`
  (trivial, reuses the same per-section renderer the read-only view
  already uses), `render_document_html` (native HTML per block type —
  real `<table>`/`<ul>` elements, not markdown syntax dumped into a page),
  `render_document_docx` (native `python-docx` objects — real Word
  headings/tables/bulleted lists), `render_document_pdf` (renders the HTML
  output via `weasyprint` with an embedded stylesheet). All four walk the
  exact same typed `content_blocks` every other pass in this feature
  reads from, and none of them are persisted — plan §11.7: a stored
  export would go stale the instant a section is edited or patched, so
  every export request re-renders fresh from whatever's in the database
  right now.
- **`POST /api/v1/sow/documents/{id}/export`** `{format: md|docx|pdf}` —
  streams the file back directly with a `Content-Disposition: attachment`
  header (no stored download URL — nothing is stored to link to). Cheap
  and unlimited, no rate limit (pure rendering, no LLM call).
- **`POST /api/v1/sow/documents/{id}/send-to-checkpoints`** — renders the
  current version to markdown and wraps it as a `sow`-type
  `DesignArtifact` through the **exact same creation path**
  `app/api/v1/visual_audit.py::upload_sow` already uses (sha256 Memory
  Bank dedupe, same `{data_dir}/sow/` storage layout, same
  enqueue-after-commit call into the existing, unmodified
  `sow_ingest.ingest_sow_task`) — zero duplicated parsing logic. Requires
  both the `sow` permission (to act on the document) and `vibe_testing`
  (the target pipeline's own upload endpoint requires it — this shouldn't
  grant indirect access to a surface the caller doesn't otherwise hold),
  and checks `VISUAL_AUDIT_ENABLED` (that pipeline's own flag, distinct
  from `SOW_ENABLED`) is on, so it can't create an artifact nothing is
  able to process.
- **New dependencies**: `python-docx==1.2.0` (pure Python, no system
  libs), `weasyprint==69.0` (needs system Pango/Cairo/GDK-pixbuf libraries
  at runtime — added `libpango-1.0-0 libpangocairo-1.0-0
  libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-liberation` to
  `docker/Dockerfile.backend`, same category of change as the existing
  `ffmpeg` addition for video stills; weasyprint imports fine without
  these but fails at `write_pdf()` time, not at import time, which is why
  this needed to be caught explicitly rather than assumed).
  **Broke the real `docker compose build` the first time it was run**:
  the package was originally named `libgdk-pixbuf2.0-0` (no hyphen before
  the version) — correct for Debian 12 "bookworm", but `python:3.11-slim`
  now resolves to Debian 13 "trixie", which renamed it to
  `libgdk-pixbuf-2.0-0` and has since dropped the old name's transitional
  package entirely, so `apt-get install` failed with "no installation
  candidate." This wasn't something the sandbox this Dockerfile was
  authored in could catch (no Docker available there) — fixed after the
  user hit it on the actual rebuild and reported the exact error.
- **Frontend**: `/sow/[id]` gained an Export row (`.md`/`.docx`/`.pdf`
  buttons that fetch, read the response as a blob, and trigger a
  synthetic download — no server-stored file to link to) and a "Send to
  Vibe Testing" button showing the resulting message, including the
  "already sent, reusing analysis" case. Both act on the document's
  CURRENT version specifically (matching the backend's own scope), shown
  once above the version list rather than per-version.

**Verified — genuinely, not just at import level:** all four renderers in
`sow_export.py` were actually exercised end-to-end against fixture section
data in the build sandbox (both `python-docx` and `weasyprint` happened to
be installable and runnable there): markdown and HTML output checked for
correct content; **HTML-escaping specifically verified** (a `<dashboard>`
string in fixture content came back as `&lt;dashboard&gt;`, confirming
user/LLM-authored text can't break out of the generated HTML structure);
the DOCX output was round-tripped — re-opened with `python-docx` itself
and checked for the expected paragraphs/table; the PDF output was
confirmed to start with the `%PDF-` magic bytes and weasyprint's own
render log showed a real successful multi-step layout+write. Also: `py_
compile` on every changed file; `app.main` imports cleanly with 105 routes
(up from 103 — two new endpoints); OpenAPI schema shows 18 `/sow` paths; a
live `TestClient` sweep confirms both new endpoints return `401` with no
auth header; `alembic heads` still resolves to a single head — confirmed
no migration was needed (no schema change this phase) rather than
assumed; no new Celery task was needed (export is synchronous rendering,
send-to-checkpoints reuses the existing `ingest_sow_task` unmodified,
confirmed still registered); new/changed frontend code re-parsed cleanly
with `@babel/core`. **Not verified:** the sandbox's `python-docx`/
`weasyprint` ran against Python 3.10 and whatever system libraries happen
to already be present there — NOT the actual `python:3.11-slim` target
image, so the Dockerfile system-package list is still unverified against
a real build (the user will rebuild and can confirm); no live
`send-to-checkpoints` → `ingest_sow_task` → checkpoint-extraction round
trip was run against a real Postgres/Celery worker in this sandbox.

### 2026-07-20 (earlier) — SOW Creation & Rewrite: Phase 5 (structured editor + version diff)

**Added**, on top of Phase 4's audit pass (see entry below): the ability
to hand-fix a generated section instead of only ever regenerating the
whole document, and a way to see what actually changed between two
versions without opening each one separately.

- **`PATCH /api/v1/sow/documents/{id}/sections/{section_key}`** (new) —
  always edits the document's `current_version_id` (no `version_id` in
  the path — editing an older, non-current version isn't supported yet;
  doing so meaningfully would need the full patch/rewrite reconciliation
  Phase 7 brings, so it's left out rather than half-built). Every block in
  the request is re-validated server-side through the exact same
  `_validate_block` function `sow_drafting`'s LLM output goes through — a
  malformed hand-edit gets `422`, never a silently-saved section the
  renderer/export can't handle later. Sets `edited_by_human`/`edited_at`
  and clears `coverage_score`/`coverage_gaps` to `null`, since a Phase 4
  audit result describes the *pre-edit* content and a stale score would
  mislead more than an honest "not yet audited." If the section had
  `status='error'`, a successful edit flips it to `done` and — if that was
  the version's last remaining error — promotes the version from
  `done_with_errors` back to `done` and the document from `error` back to
  `ready`, so the UI stops flagging a problem the user just fixed by hand.
- **Structured block editor** (`/sow/[id]`) — each section can now be
  edited in place: heading (level + text), paragraph, control (element
  type, label, behavior), bullet list (one item per line), table
  (editable grid with add/remove row and column), and callout (tone +
  text), each with reorder (↑/↓) and delete, plus an "add block" picker.
  Deliberately NOT a raw markdown textbox — every block a user can produce
  through this editor is exactly the block-type contract
  `_validate_block` accepts, so a hand-edit is never less structured (and
  therefore less diffable/exportable/checklistable) than an LLM draft.
  Editing is only offered while viewing the current version — the PATCH
  endpoint always targets `current_version_id` regardless of which
  version's page you're looking at, so an Edit button on a historical
  version would silently edit the wrong document state; the frontend
  hides it there instead of letting that trap happen.
- **Version diff** — a "Compare with previous" toggle next to the version
  list diffs the selected version against the one immediately before it
  by `version_number` (there's no `parent_version_id` lineage yet — every
  version so far is a `full_generation`, Phase 7 patches don't exist —
  so "previous by number" is the only comparison that means anything
  today). Purely client-side, no new endpoint: fetches both versions'
  existing detail responses and runs a hand-rolled O(n·m) LCS line diff
  per matching `section_key` (capped at 1500 lines/section to avoid a
  pathological table blowing up the tab — falls back to "changed, too
  large to diff inline" past that). Sections present in only one version
  are flagged Added/Removed; unchanged sections collapse to a one-line
  note instead of repeating their full text.
- **Generate-time warning** — if the current version has any hand-edited
  section, clicking "Generate SOW" now confirms first: a full generation
  always creates a brand-new version from scratch and does not carry
  hand-edits forward (only Phase 7's rewrite/patch flow will respect
  `edited_by_human`), so this prevents that from being a silent surprise.
- **No new migration** — `edited_by_human`/`edited_at` were already
  columns on `sow_sections` from the Phase 0 migration, same as Phase 4's
  coverage columns.

**Verified:** `py_compile` on every changed file; `app.main` imports
cleanly with 103 routes (up from 102 — one new `PATCH` route); OpenAPI
schema regenerates with 16 `/sow` paths; a live `TestClient` sweep
confirms the new `PATCH .../sections/{key}` endpoint and `POST
.../generate` both still correctly return `401` with no auth header;
`alembic heads` still resolves to a single head
(`0029_sow_document_sources`) — confirmed no migration was needed rather
than assumed; the new/changed frontend code (block editor, diff view, and
the diff utility) re-parsed cleanly with `@babel/core` (JSX/TSX mode).
**Not verified:** no live Postgres/Celery call was made for this change in
this sandbox — actually saving a hand-edit, watching a section flip out of
`error`, and reading a real diff between two generated versions can only
be confirmed against your running environment. Recommend: rebuild
`backend` (no `celery_worker` changes this phase — editing and diffing
are both synchronous API/frontend work, no new Celery task), then edit a
section, save it, and try "Compare with previous" once you have two
versions.

### 2026-07-20 (earlier) — SOW Creation & Rewrite: Phase 4 (completeness audit + coverage UI)

**Added**, on top of Phase 3's generation pipeline (see entry below): an
independent completeness audit pass and coverage scoring, so a generated
SOW section carries evidence it was actually checked against its source
facts — not just a hope that the drafting prompt was followed.

- **`app/services/sow_audit.py`** (new) — `audit_section(heading, blocks,
  facts) -> (coverage_score, coverage_gaps, model_used)`. This is a
  **separate LLM call from the one that drafted the section** (plan §2
  Pass 3: "self-grading is unreliable") — a fresh reviewer re-reads the
  drafted text against the original facts and judges, fact by fact,
  whether each was actually represented (for a `ui_element` fact: control
  type, label, *and* behavior all present and accurate, not just
  mentioned). Any fact index the audit response doesn't explicitly address
  is treated as a gap, never assumed covered by omission — the same
  never-drop-silently principle used everywhere else in this feature
  (`group_ledger_into_sections`'s auto-recovery, `draft_section`'s own
  structural safety net). This is a *stronger* check than that structural
  net: `draft_section` can only confirm a `control_spec` block exists for
  a fact's index; the audit judges whether the actual prose is correct.
- **Wired into `generate_sow_task`** as its own job stage
  (`SowJobStage.audit`) between drafting and assembly for each functional
  section. Degrades gracefully — an audit failure never fails a section
  that drafted successfully; it just leaves `coverage_score`/
  `coverage_gaps` `null` (an honest "not audited," never a fabricated
  score) and logs a warning.
- **Scope decision:** only the functional (grouped) sections are audited.
  Project Overview/Scope of Work are narrative summaries by design ("a
  table of contents... not a restatement of every detail" — Phase 3's own
  prompt) and the five templated trailing sections have no facts to audit
  against at all; both permanently keep `coverage_score = null` rather
  than being forced through a checklist audit that measures the wrong
  thing for that content type. Documented in `sow_audit.py`'s module
  docstring so this reads as a decision, not a gap.
- **No new migration** — `sow_sections.coverage_score`/`coverage_gaps`
  were already columns on the table from the Phase 0 migration
  (`0028_sow_foundation`), sized ahead of time for exactly this phase, as
  the original plan intended.
- **API**: `SowSectionOut` gained `coverage_gaps` (`coverage_score`
  already existed as an unpopulated stub since Phase 3).
- **Frontend**: the `/sow/[id]` read-only section view (built in Phase 3)
  gained a color-coded coverage badge per section (green ≥90%, amber
  70–89%, red <70%, hidden for sections that were never audited) and, when
  the audit found gaps, a listed panel naming exactly which facts weren't
  found covered and why — visible without needing to hunt through the
  rendered prose.

**Verified:** `py_compile` on every new/changed file; `app.main` imports
cleanly (102 routes — unchanged, this phase added no new endpoints, only
new logic and one new response field); OpenAPI schema regenerates
cleanly and confirms `SowSectionOut` now carries `coverage_gaps`; a live
`TestClient` sweep confirms `POST .../generate` and
`GET .../versions/{id}` still correctly return `401` with no auth header;
Celery confirms `sow_generation.generate_sow_task` still registers; no
`alembic` changes were needed (confirmed the required columns already
existed rather than assuming); new/changed frontend code re-parsed
cleanly with `@babel/core` (JSX/TSX mode). **Not verified:** no live
Postgres/Celery/Gemini call was made for this specific change in this
sandbox — the audit's actual judgment quality (does it correctly catch a
real missing button, does it avoid false negatives on facts that *are*
covered) can only be judged against a real generation run. This phase does
add one more LLM call per functional section (drafting + audit, up from
just drafting), so full generation takes proportionally longer — expected
and consistent with the plan's own "minutes, not seconds" framing for a
thorough generation. Recommend: `docker compose build backend
celery_worker && docker compose up -d backend celery_worker`, then
regenerate an existing document (or a new one) and check whether the
coverage badges/gaps look right against a section you can eyeball
yourself.

### 2026-07-20 (earlier) — SOW Creation & Rewrite: Phase 3 (drafting + assembly + generation)

**Added**, on top of Phase 1's ledger (see entries below): the actual
generation pipeline that turns `sow_requirements_ledger` facts into a full,
versioned, section-by-section SOW document. No editor yet — this phase is
generate + read-only view, matching the plan's phase table.

- **`app/services/sow_drafting.py`** — Pass 2a `group_ledger_into_sections`
  (asks the model to group ledger facts by *index*, never by UUID, to avoid
  hallucinated IDs; any index the grouping pass misses is swept into an
  auto-generated "Additional Items" section rather than silently dropped)
  and Pass 2b `draft_section` (drafts one section's typed `content_blocks` —
  heading/paragraph/control_spec/bullet_list/table/callout, per plan §11.6 —
  and enforces exactly one `control_spec` per `ui_element` fact via a
  `fact_index` back-reference; any `ui_element` fact the model's draft never
  referenced gets auto-appended as a flagged callout, a completeness safety
  net built into drafting itself, ahead of the formal audit pass Phase 4
  adds). `render_blocks_markdown` renders on demand, never stored (§11.7),
  so displayed markdown can never go stale relative to the structured
  blocks.
- **`app/services/sow_assembly.py`** — Pass 4: LLM-drafted Project
  Overview + Scope of Work (synthesized only from `feature`/`decision`
  facts), plus five templated trailing sections (Out of Scope, Assumptions,
  Dependencies, Exclusions, Sign-off & Acceptance Criteria) that are
  **deliberately never LLM-drafted** — hallucinating contractual
  scope-boundary language is unsafe in a way that functional requirements
  grounded in actual meeting/design material aren't. These ship as
  explicit "Not yet defined" placeholders for a human to fill in.
- **`app/workers/tasks/sow_generation.py`** (`generate_sow_task`) —
  orchestrates grouping → per-section drafting → assembly with a genuine
  partial-failure model (plan §11.5): each section is drafted and
  persisted independently; one section erroring doesn't discard sections
  that succeeded. Final `SowVersionStatus`/`SowDocumentStatus`/
  `SowJobStatus` is `done` only if every section succeeded,
  `done_with_errors` if some did and some didn't, `error` only if literally
  every section failed.
- **API**: `POST .../documents/{id}/generate` (concurrency-guarded via
  `SELECT ... FOR UPDATE` on the document row — plan §11.3 — a second
  concurrent call while one is already running gets `409`, not a silent
  double-run; `400` if the ledger is empty), `GET .../generation` (poll the
  latest job), `GET .../versions` (list), `GET .../versions/{id}` (full
  detail — every section's `content_blocks` and on-demand
  `rendered_markdown`).
- **Frontend**: `/sow/[id]` gained a "Generate SOW" section — a Generate
  button (enabled once ≥1 source has finished extraction), live job-status
  polling, a version picker, and a read-only rendered view of the selected
  version's sections, each with its own status badge so a `done_with_errors`
  version shows exactly which sections failed and why, instead of hiding
  the gap behind an overall "success."

**Fixed during self-review, before it shipped** (same discipline as
Phases 0/1 — re-reading the diff against this codebase's own transaction
and worker-crash conventions, not just confirming it imports):
- `generate_document`'s original version-then-job sequence used **two
  separate commits** inside one try block (create version + set
  `document.status='generating'`, commit; then create the job row, commit
  again). If the second commit failed, the first had already landed —
  leaving the document permanently stuck in `'generating'` with no job and
  no task ever enqueued, which the `SELECT ... FOR UPDATE` concurrency
  guard would then block from ever generating again. No reconciliation
  sweep covers stuck `sow_documents` rows (only `sow_document_sources`
  does, from Phase 1). Fixed by flushing the version to get its id without
  committing, then creating both the version and the job and flipping the
  document status in a **single** commit — either both rows and the status
  change land together, or a rollback discards all three cleanly.
- `generate_sow_task` had no idempotency guard against Celery's own
  `task_acks_late=True` setting (`celery_app.py`, applies to every task in
  this codebase): a worker crash or restart mid-generation causes the
  broker to redeliver the unacked task, which re-runs `generate_sow_task`
  from scratch with the same `document_id`/`version_id` — but nothing
  cleared the `SowSection` rows a crashed prior attempt had already
  inserted, so a redelivered run would duplicate every section rather than
  cleanly redoing the work. Fixed by clearing any existing sections for the
  version at the very start of the task, mirroring `sow_ledger.py`'s
  `_clear_prior_facts` pattern for the same class of problem on the
  retry-a-source path.

**Verified:** `py_compile` on every new/changed file; `app.main` imports
cleanly (102 routes, up from 98 pre-Phase-3); full OpenAPI schema
generates with all 15 `/sow` paths (12 document-scoped); a live
`TestClient` sweep confirms `POST .../generate`, `GET .../generation`,
`GET .../versions`, and `GET .../versions/{id}` all correctly return `401`
with no auth header, including after the atomic-commit fix above; Celery
confirms `sow_generation.generate_sow_task` registers; `alembic heads`
resolves to a single head (`0029_sow_document_sources`) — Phase 3 added no
new tables, only new logic over the Phase 0 schema, so no new migration
was needed; the new frontend code was parsed with `@babel/core` +
`@babel/preset-typescript` (JSX/TSX mode) to confirm it's syntactically
valid, and hook ordering was manually re-checked (all hooks unconditional,
before any early return, no stale-closure references). **Not verified:**
no live Postgres/Celery worker/Gemini call was made in this sandbox — an
actual end-to-end generation run (does the drafted SOW really read well,
does the completeness safety net actually catch a missed control) can only
be judged against your running `docker compose` environment. No new env
vars were introduced this phase, so no `docker-compose.yml` changes were
needed (learned from the Phase 1 `SOW_ENABLED` miss — checked explicitly
this time rather than assumed). Recommend: `docker compose build backend
celery_worker && docker compose up -d backend celery_worker`, then attach a
source and click Generate on a real document to see the pipeline run
end-to-end for the first time.

### 2026-07-20 (later still) — SOW Creation & Rewrite: "SOW authoring is not enabled" despite SOW_ENABLED=true in .env

**Problem reported:** `/sow` showed "SOW authoring is not enabled" and the
browser console showed `GET /api/sow/documents 404`, even though
`SOW_ENABLED=true` was set in `.env` and `backend` had been rebuilt.

**Root cause:** `docker-compose.yml` does not pass `.env` through to
containers wholesale — each service's `environment:` block is an explicit
allowlist (`VISUAL_AUDIT_ENABLED: ${VISUAL_AUDIT_ENABLED:-false}`, etc.);
`.env` is only used by Compose to *resolve* those `${VAR}` references, not
to inject arbitrary additional variables. `SOW_ENABLED`,
`SOW_MAX_RECORDING_MB`, and `SOW_MAX_RECORDING_MINUTES` were added to
`.env`/`.env.example` during Phase 0/1 but never added to
`docker-compose.yml` itself, so the running container's actual environment
never had them regardless of what `.env` said — `os.environ.get
("SOW_ENABLED", "")` inside the container saw nothing and `_feature_enabled()`
correctly (from its own perspective) 404'd.

**Fix (`docker-compose.yml`):** added all three vars to both `backend`'s
and `celery_worker`'s `environment:` blocks, same
`${VAR:-default}` convention as every existing entry.

**Verified:** YAML re-parsed successfully via PyYAML; confirmed both
service blocks now carry `SOW_ENABLED`/`SOW_MAX_RECORDING_MB`/
`SOW_MAX_RECORDING_MINUTES`. **Not verified against a live container** —
this requires `docker compose up -d backend celery_worker` (a plain
recreate picks up the compose file change; combine with `build` if you
haven't yet rebuilt with the Phase 1 code, since `celery_worker` specifically
was never rebuilt in the prior round).

### 2026-07-20 (later same day) — SOW Creation & Rewrite: Phase 1 (source ingestion + raw ledger dump)

**Added**, on top of Phase 0's foundation (see entry below and
`SOW_FEATURE_PLAN.md` §8): the first real ingestion pipeline. A SOW
document can now have meeting transcripts, meeting recordings, and design
references attached, each independently extracted into
`sow_requirements_ledger` — the flat, exhaustive fact/UI-element checklist
later phases draft a SOW from and audit completeness against.

- **New table** `sow_document_sources` (migration `0029_sow_document_sources`)
  — Phase 0 shipped the ledger table but nothing represented "this file is
  attached to this document, and here's its extraction status," so nothing
  could be listed, retried, or detached. Also extends `artifact_type_enum`
  with `meeting_transcript`/`meeting_recording`.
- **`app/services/sow_ledger.py`** — the actual extraction logic:
  `extract_ledger_from_text` (transcripts, chunked via the same
  `design_ingest.chunk_text` the SOW Checkpoints pipeline uses),
  `extract_ledger_from_recording` (reuses `video_ingest.py`'s Gemini Files
  API upload/poll/still-frame machinery directly rather than
  reimplementing it — only the prompt and parsed response shape differ from
  `digest_video`), and `extract_ledger_from_image` (vision call via
  `llm_router.complete` for design references). Every prompt explicitly
  demands exhaustive per-control extraction (buttons/dropdowns/filters/
  checkboxes/toggles/sliders/three-dot menus/tabs/modals) and a
  `source_ref` pointer (timestamp/quote/location) back to where each fact
  came from — traceability the plan requires but which needed to be
  designed into the prompt and validator explicitly, not assumed.
- **Three Celery tasks** (`app/workers/tasks/sow_ledger.py`) plus a
  reconciliation sweep (`app/workers/tasks/sow_reconcile.py`, scheduled
  every 5min) that recovers any source stuck 'processing' if a worker dies
  mid-extraction — mirrors `visual_qa_reconcile.py` exactly.
- **API**: `POST .../sources/transcript` (file or pasted text),
  `POST .../sources/recording` (rate-limited 10/hour, size-capped via
  `SOW_MAX_RECORDING_MB` default 300, duration-capped via
  `SOW_MAX_RECORDING_MINUTES` default 60 — checked via `ffprobe` on a temp
  file *before* the upload is ever registered as an artifact),
  `POST .../sources/design` (attach an existing `figma_png` artifact or
  upload a new PNG), `GET .../sources`, `DELETE .../sources/{id}`,
  `GET .../ledger` (the raw dump, filterable by `fact_type`).
- **Frontend**: `/sow/[id]` document detail page — upload panels for all
  three source kinds, a sources table with live status polling while
  anything is pending/processing, and the raw ledger table. Document
  titles in `/sow`'s list now link here.
- **Deliberate Phase 1 simplifications** (not oversights — see
  `sow_ledger.py`'s module docstring and `.env.example`): ledger extraction
  is scoped per (document, artifact) rather than deduplicated across
  documents sharing the same uploaded file — re-attaching the same
  transcript to a second document re-runs extraction rather than copying
  facts; and extraction reuses the existing shared
  `VISUAL_LLM_PRIMARY`/`FALLBACKS` (text/image) and
  `VISUAL_VIDEO_MODEL`/`FALLBACK` (recordings) chains rather than
  introducing a separate `SOW_LLM_*` namespace the original plan proposed
  — avoids config sprawl before there's a concrete reason for SOW
  extraction to diverge from the rest of the Visual QA model chain.

**Fixed during implementation, before it shipped** (same discipline as
Phase 0 — a second read of the diff against this codebase's own
conventions, not just a syntax check):
- `_save_facts` originally assigned raw strings (e.g. `"ui_element"`)
  directly to the `Enum`-typed `fact_type`/`element_type` columns. Every
  existing write to an Enum column in this codebase (`artifact_type=
  ArtifactType.video`, etc.) always assigns an actual enum member, never a
  raw string — changed to match, rather than trust untested implicit
  coercion behavior.
- The three Celery tasks originally read `source.artifact` as if
  `SowDocumentSource` had an ORM `relationship()` to `DesignArtifact` — it
  doesn't; no model in `app/models/sow.py` or `visual_qa.py` declares
  `relationship()` anywhere in this codebase, by convention (explicit
  queries only). Changed to `session.get(DesignArtifact, source.artifact_id)`
  in all three tasks — would have been an `AttributeError` on every single
  extraction run.
- `source_ref` was defined as a ledger-row column in Phase 0 but Phase 1's
  first draft never actually asked the LLM for it or validated/threaded it
  through — silently-always-empty traceability data despite the plan
  explicitly promising it. Added `source_ref` to the extraction prompt,
  validator, and storage path.

**Verified:** `py_compile` on every new/changed file; `app.models` /
`app.schemas.sow` / `app.api.v1.sow` / `app.workers.tasks.sow_ledger` /
`app.workers.tasks.sow_reconcile` all import cleanly against this repo's
real `.env`; `app.main` builds its full OpenAPI schema with all 11 new
routes and no schema-generation errors (would have surfaced a File/Form
mixing conflict, if one existed); `alembic history` resolves
`0029_sow_document_sources` as head, chained correctly off
`0028_sow_foundation`; all 11 new endpoints correctly return `401` via a
live `TestClient` request with no auth header (proves the full
routing/multipart-parsing/rate-limiter chain is wired, not just that the
module imports); Celery confirms all 4 new tasks
(`sow_ledger.extract_{transcript,recording,design}_ledger_task`,
`sow_reconcile.reconcile_stale_sow_sources`) register and the beat
schedule picks up the new reconciliation entry; new frontend files parse
cleanly (`@babel/parser`, JSX). **Not verified:** no live Postgres/Celery
worker/Gemini API call in this sandbox — the extraction pipeline's actual
output quality (does it really catch every button/dropdown/checkbox) can
only be judged against a real transcript/recording/design file, which
needs your running `docker compose` environment. Recommend: rebuild
`celery_worker` (new task modules — `backend` alone isn't enough this
time) and `frontend`, run `alembic upgrade head` (automatic on `backend`
container start per `start.sh`), then attach one real source of each kind
to a test document and read through `GET .../ledger` for yourself before
trusting it on a real project.

### 2026-07-20 — SOW Creation & Rewrite: Phase 0 foundation (document CRUD, schema, nav entry)

**Added** (new feature, see `SOW_FEATURE_PLAN.md` at the repo root for the
full design and phased rollout): the first phase of SOW Creation & Rewrite
— generating/rewriting a Statement of Work from meeting transcripts,
meeting recordings, and design references, detailed enough (every button,
dropdown, filter, checkbox, toggle, slider, three-dot menu) to feed
straight into the existing SOW Checkpoints extractor for vibe testing.

- `backend/alembic/versions/0028_sow_foundation.py` — `sow_documents`,
  `sow_document_versions`, `sow_sections`, `sow_requirements_ledger`,
  `sow_generation_jobs`, plus two new `design_artifacts.artifact_type`
  values (`meeting_transcript`, `meeting_recording`) reusing the existing
  sha256 Memory Bank dedupe. Idempotent (matches `0021_add_sow_parts.py`'s
  guard convention); full schema shipped in one migration, sized for every
  later phase (status/error columns per section and job, human-edit
  protection flags, coverage scoring) rather than shipping a weaker first
  pass and re-migrating immediately after.
- `backend/app/models/sow.py`, `backend/app/schemas/sow.py`,
  `backend/app/api/v1/sow.py` — document CRUD only in this phase
  (create/list/get/rename/soft-delete), behind a new `sow` permission
  (deliberately distinct from `vibe_testing`) and a new `SOW_ENABLED`
  feature flag (same convention as `VISUAL_AUDIT_ENABLED`, default off but
  set `true` in `.env`/`.env.example`/`.env.sample` for local dev).
  `project_id` is a list filter only, not an access boundary — this
  codebase has no per-project membership/ACL anywhere (checked
  `app/api/v1/projects.py` before assuming otherwise), so this matches how
  every other project-scoped resource already behaves.
- `frontend/src/app/sow/page.jsx` + Next.js proxy routes
  (`frontend/src/app/api/sow/documents/**`) — functional (not a
  placeholder) create/rename/delete UI for document shells. New "SOW" nav
  entry in `AppShell.jsx`, gated by the `sow` permission.
- **Not yet implemented, by design** (see plan §8): source upload,
  generation pipeline, editor, versions/diff, export, rewrite/patch,
  send-to-Vibe-Testing hand-off. `/sow` currently only manages empty
  document shells — nothing generates SOW content yet.

**Fixed during implementation, before it shipped:** the delete endpoint was
initially written to return `204 No Content` (REST-conventional), which
would have silently broken the shared `apiDelete()` frontend client — it
unconditionally calls `res.json()` on every response, a convention
confirmed by checking every other `DELETE` endpoint in this codebase (all
return `200` + a JSON body, e.g. `projects.py::delete_project`). Changed to
match. Also: the migration's `downgrade()` originally dropped
`sow_document_versions` before dropping the `current_version_id` foreign
key that `sow_documents` holds against it (added separately in `upgrade()`
to break the circular dependency between the two tables at create time) —
Postgres would have rejected that `DROP TABLE`. Fixed by dropping that
constraint explicitly first. Neither of these was caught by static
type/syntax checks — both came from actually re-reading the diff against
this codebase's existing conventions before calling it done.

**Verified:** `python -m py_compile` on every new/changed backend file;
`app.models` imports cleanly and all 5 new tables register on
`Base.metadata`; `app.schemas.sow` and `app.api.v1.sow` import and
construct correctly against this repo's real `.env`; the new router
carries exactly the 5 expected routes with no path collision against the
existing `/api/v1/visual-audits/sow*` endpoints; `app.main` imports with
all 92 routes wired (was 87 before); `alembic history` resolves the new
revision as head, chained correctly off `0027_android_platform`. New
frontend files parse cleanly (`@babel/parser`, JSX enabled) and the
`ui/select` import names were checked against `select.tsx`'s actual
exports rather than assumed. **Not verified:** the migration has not been
run against a live Postgres instance (no Postgres/Docker available in the
sandbox this was built in) — run `alembic upgrade head` in the real dev/
docker environment as the first real-world check before trusting this in
anything beyond local testing.

### 2026-07-16 — Bypass credential profile: cookie injected but session never actually authenticated

**Problem reported:** the "IG Login bypass" credential profile injected its
auth cookie without error, but the app still showed the public marketing
homepage and a real Sign In form — never the Dashboard. The AI agent, whose
goal text also told it to click Sign In, ended up trying (and failing) a
real manual login instead of starting pre-authenticated.

**First hypothesis (wrong — corrected same day):** `_resolve_bypass_profile()`
POSTs to `/admin-login-by-api-key` with only the `X-API-Key` header, no body.
Since `ig_automation/libs/hopscotch_client.py` sends `{"email", "otp"}` in its
POST body and hard-fails without them, the initial fix made email/otp
required for this endpoint too. **This was checked against the actual
endpoint behavior and confirmed wrong** — the X-API-Key header alone grants
access directly; hopscotch_client.py's email/otp are specific to its own
flow, not a requirement of the endpoint itself. That change was reverted in
full (schema, backend POST, and the dialog's Email/OTP fields) so the
existing "IG Login bypass" profile keeps working with its stored credentials
exactly as they are — no profile recreation needed.

**What's still true and was kept:** the "Target / App URL" field's
placeholder/help text now calls out using the actual post-login destination
(e.g. `.../dashboard`) rather than the public marketing homepage — the
homepage renders the same Sign In/Sign Up nav regardless of auth state, so
landing there after cookie injection proves nothing either way. This is a
docs-only nudge for *new* profiles; it doesn't change any request or
validation behavior.

**Still open:** the actual reason the existing profile isn't landing on the
Dashboard hasn't been re-diagnosed yet post-revert — the video evidence
(marketing homepage after cookie injection, `test@interviewgod.ai` bounced
to "Create a new account") still needs an explanation that doesn't involve
email/otp. Candidates not yet ruled out: the profile's stored `target_url`
pointing at the marketing root instead of `/dashboard`, a cookie
domain/name mismatch, or something in how the target app's frontend reads
the cookie. Needs another look at the actual request/response (e.g. the
`auth_token` value and the app's own session-check call) rather than more
guessing from the client-side code alone.

### 2026-07-16 — Vibe Testing "New Test" panel: dead SSE token, 401 startup noise, Environment/Credential Profile ordering

**Problem reported:** four bugs in the "New Vibe UI Test" quick-run panel: (1)
a submitted run never advanced past "Initialising…" / "Waiting for first
screenshot…" even though it was actually executing server-side; (2) the
Network tab showed a burst of 401s immediately followed by 200s for the same
endpoints on every page load; (3) the Credential Profile picker could be
filled in before an Environment was chosen; (4) there was no way to unlock
the ad-hoc URL/Email/Password fields without picking one of the pre-seeded
backend "environments".

**Root causes:**
- **(1)** `frontend/src/app/ai-testing/page.tsx`'s SSE subscription read the
  live-run auth token via `localStorage.getItem("aep_access_token")`. The
  access token was moved to in-memory-only storage (`lib/api.ts`) a while
  back for XSS hardening and is never written to `localStorage` anymore, so
  that read always returned `""`. `EventSource` can't send an
  `Authorization` header, so the token is passed as `?token=`, forwarded by
  the Next.js proxy — with an empty token the proxy forwarded no header,
  FastAPI's `get_current_user` 401'd, and per the SSE spec a non-200
  response makes `EventSource` fire `onerror` (never `onmessage`); the
  handler just closed the connection. The run itself was running fine in
  Celery the whole time — the UI just never heard about it. The identical
  bug existed in `frontend/src/app/execute/page.jsx`'s stream connection.
- **(2)** The access token being in-memory-only means every full page
  load/navigation starts with no token, so the first wave of `useQuery`
  calls always fired before `Providers.jsx`'s reactive
  `useEffect`-based pre-emptive refresh could run — React fires a
  component's mount effects before its ancestors', so by the time
  `Providers` got a turn, the damage (a guaranteed 401 per query) was
  already done. The refresh itself was correctly de-duplicated, so this
  never broke a session — it was pure noise, but real noise.
- **(3)/(4)** The Credential Profile control was shown whenever `!selectedEnv`
  (i.e. *no* Environment picked) OR the environment was the bypass-capable
  one — so "nothing chosen" and "bypass environment chosen" both rendered
  the same picker, with no way to reach the ad-hoc URL/Email/Password path
  except by picking one of the other pre-seeded environments.

**Fix implemented:**
- `getAccessToken()` (`lib/api.ts`) is now the single source of truth for
  the SSE token in both `ai-testing/page.tsx` and `execute/page.jsx`, with a
  one-time `refreshAccessToken()` fallback if it's momentarily unset.
- `utils/apiClient.js`: the pre-emptive refresh-cookie redemption moved from
  a React effect in `Providers.jsx` to **module-load time** — it now runs
  the instant `apiClient.js` is evaluated (part of the JS module graph load,
  strictly before React mounts anything), so it's already in flight before
  any component's mount-time query can fire. `apiFetch` now also awaits an
  in-flight refresh before sending a request, instead of only reacting
  after that request 401s. `Providers.jsx`'s now-redundant reactive effect
  was removed.
- `ai-testing/page.tsx`: Environment is now a mandatory gate — with nothing
  selected, neither the Credential Profile picker nor the ad-hoc fields
  render (a "Select an Environment first" placeholder shows instead). A new
  synthetic **"No Environment"** entry was added to the top of the
  Environment dropdown (never sent as `project_id`) which, like any other
  non-bypass environment, unlocks the "Website without/with login"
  URL/Email/Password fields — satisfying the standing ask for a way to
  ad-hoc test a site with no pre-configured environment. Also fixed: the
  Credential Profile list query was appending the "No Environment" sentinel
  as `?project_id=` (a 422 against the backend's UUID-typed filter) — now
  only real environment ids are passed.

**Verified:** rebuilt and ran the `frontend` container; confirmed in-browser
that (a) all `/api/ai-testing/*` and `/api/v1/visual-audits/*` calls return
`200` on first attempt on a fresh login (no more 401→200 pairs), (b) the
Credential Profile control is inert until an Environment is chosen, (c)
"No Environment" reveals the URL/Email/Password fields, (d) a live "No
Environment" + "Website without login" run against `https://example.com`
streamed real step events and a live screenshot into the Live Action Log
(previously would have hung forever), and (e) the existing "IG Automation"
bypass Credential Profile flow still works unchanged.

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
