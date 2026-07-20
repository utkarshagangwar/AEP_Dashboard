# SOW Creation & Rewrite — Engineering Plan

**Status:** Proposal, pending approval — no code written yet.
**Author:** Claude (Cowork), for Utkarsh Gangwar
**Date:** 2026-07-20
**Scope confirmed with stakeholder:**
- Meeting input accepted as **both** pasted/uploaded transcript text **and** raw meeting recording (audio/video).
- On generation, user is offered **all three** consumption paths: in-app editable document, exportable file (.docx/.pdf/.md), and one-click send into the existing Vibe Testing SOW-checkpoint extractor.
- Rewrite supports **both** section-level patch (default) and full regeneration (on demand).

---

## 1. Why this is harder than "call an LLM and write a doc"

The platform's existing SOW pipeline (`design_ingest.py`) goes **SOW → checkpoints**. This feature goes the other direction: **meeting + video + design → SOW**, and that SOW is the ground truth the *same* extractor will later parse to build vibe-testing skills. That creates a hard constraint stated explicitly by the stakeholder:

> If anything goes missing in the SOW, it will also be missing in vibe testing, and this will impact the business.

A single-shot "summarize the meeting into a document" LLM call **will** drop things — free-text generation silently under-reports UI chrome (this is a proven failure mode already logged in this repo's own README: the video-ingestion mismatch bug on 2026-07-12 was exactly a model under-weighting small persistent UI elements). So this can't be architected as one prompt. It has to be architected as an **extraction-then-verification pipeline** with a machine-checkable completeness gate, matching the platform's existing philosophy (see `design_ingest.py` docstring: *"Reliability rules: ... never a silent empty result"*).

This plan follows that same philosophy end to end.

---

## 2. Pipeline architecture (the core design decision)

Four passes, not one:

```
Sources                     Pass 1              Pass 2              Pass 3                Pass 4
────────────────────────────────────────────────────────────────────────────────────────────────
Transcript text    ─┐
Meeting recording   ├──▶  UI/REQUIREMENTS   ──▶  SECTION DRAFTING ──▶ COMPLETENESS   ──▶  ASSEMBLY
  (audio/video)      │    LEDGER extraction       (chunked,             AUDIT                & FORMAT
Design artifacts    ─┘    (facts + UI            per feature/page)   (draft vs ledger,
  (Figma / images/         inventory)                                 gap-fill, never
  existing SOW for                                                    silent-drop)
  rewrite)
```

### Pass 1 — Requirements Ledger extraction
Every source is normalized into one shared, structured fact table **before any prose is written**:

- **Transcript** → LLM extraction pass (reusing `llm_router.py`, same pattern as `design_ingest.py`) pulls out: features discussed, decisions made, explicit UI requirements, open questions/ambiguities.
- **Meeting recording** → reuses the existing Gemini Files API video-digest pipeline (`video_ingest.py`) almost as-is: resumable upload, poll, `generateContent`. Two additions on top of what's there today:
  1. Also transcribe **audio** (Gemini video understanding already ingests the audio track — this just means the extraction prompt asks for spoken decisions/requirements, not just on-screen action).
  2. Reuse the **still-frame extraction** trick (`ffmpeg`, already in `Dockerfile.backend`, already used in `video_ingest.py::_extract_still_frames`) — this is the fix the team already proved works for "the model undercounts small persistent UI elements." Stills get run through a dedicated **UI Element Inventory** prompt (see below), not just the general checkpoint prompt.
- **Design artifacts** (Figma export / uploaded screenshots, via existing `figma_service.py` / `design_artifacts` table) → vision pass over each frame, same UI Element Inventory prompt as video stills.
- **Existing SOW** (rewrite case only) → parsed with the *existing* `design_ingest.py` checkpoint extractor, reused unmodified, seeded into the ledger as the current baseline.

**UI Element Inventory** is the key new artifact type. It is a flat, deduplicated list, one row per control:
```json
{ "element_type": "button|dropdown|filter|checkbox|toggle|slider|three_dot_menu|tab|modal|...",
  "label": "e.g. 'Bulk delete'",
  "location": "e.g. 'Skills tab, per-row actions'",
  "source": "video_still#3 | design_frame:checkout.png | transcript",
  "behavior_notes": "free text, only what was actually observed/stated" }
```
This inventory is the **checklist** Pass 3 verifies against — not the prose itself. This is the mechanism that prevents "quality loss" on large projects: the ledger is exhaustive and mechanical; only the *writing* is chunked, never the *fact-finding*.

Ledger rows carry a `source` pointer back to the originating artifact/timestamp/frame — every claim in the final SOW is traceable, which matters both for stakeholder trust and for debugging when something looks wrong (matches the repo's existing "verified end-to-end" changelog discipline).

### Pass 2 — Section drafting (chunked)
The ledger is grouped by feature/page/module (LLM-assisted grouping, but the grouping itself is a small structured call, not prose). Each group becomes one **SOW section**, drafted independently — this is how "huge project → huge SOW, broken into parts without losing quality" is satisfied: chunking happens on *scope*, not on arbitrary character counts, so a section never gets cut mid-feature. Each section prompt is instructed to cover, exhaustively, every ledger row assigned to it: purpose, states, every button/dropdown/filter/checkbox/toggle/slider/three-dot-menu with its exact behavior, validation rules, error states, empty states.

Sections are generated as **structured content** (heading + typed blocks: prose / table / list), not raw markdown blobs — this is what makes the in-app editor and the completeness audit possible (you can't diff or checklist a wall of markdown reliably, but you can diff structured blocks).

### Pass 3 — Completeness audit (the quality gate)
A dedicated verification pass — deliberately **not** the same call that wrote the section (self-grading is unreliable) — takes {section draft, its assigned ledger rows} and checks: is every ledger row's `element_type`/`label` explicitly present in the draft? Anything missing is not silently dropped: it's appended to the section under an explicit "Additional elements (auto-recovered)" block flagged for human review, mirroring the existing platform rule that a failed/ambiguous result must never look like a normal, complete one (same principle as the `platform_match` hard-fail in `video_ingest.py`).

This pass also produces a **coverage score** per section (`ledger rows covered / total`) surfaced in the editor UI, so the QA engineer using this feature can see at a glance which sections need a manual pass before this SOW is trusted for vibe testing.

### Pass 4 — Assembly & formatting
Sections are ordered, numbered, given a standard SOW skeleton (Project Overview, Scope of Work, Out of Scope, Functional Requirements by module, Deliverables, Assumptions, Dependencies, Exclusions, Sign-off & Acceptance Criteria — matching your own SOW output standard), and rendered to the three consumption formats on demand (not eagerly — export is generated when requested, from the same structured source, so editor edits are always what gets exported).

---

## 3. Data model (additive only, new tables)

Following the existing `visual_qa.py` pattern (additive, no existing table touched):

```
sow_documents
  id, project_id (FK, nullable), title, status (draft|generating|ready|error),
  current_version_id (FK → sow_document_versions, nullable),
  created_by, created_at, updated_at

sow_document_versions
  id, document_id (FK), version_number, kind (full_generation|patch),
  parent_version_id (FK, nullable — what this patched, if a patch),
  status (pending|generating|done|error), error_message,
  generated_by_model, created_at

sow_sections
  id, version_id (FK), order_index, heading, section_key (stable id used
  across versions for diffing/patching), content_blocks (JSONB — typed
  block list), coverage_score (0-100), coverage_gaps (JSONB, list of
  ledger rows not found in prose), created_at

sow_requirements_ledger
  id, document_id (FK), source_artifact_id (FK → design_artifacts, nullable),
  fact_type (feature|decision|ui_element|open_question),
  element_type (button|dropdown|filter|checkbox|toggle|slider|
  three_dot_menu|tab|modal|other, nullable — ui_element rows only),
  label, location, behavior_notes, source_ref (e.g. "video_still#3",
  "transcript:00:14:32"), assigned_section_key (nullable until grouped),
  created_at

sow_generation_jobs
  id, document_id (FK), version_id (FK), stage (ledger_extraction|
  drafting|audit|assembly), stage_progress (e.g. "7/12 sections"),
  status (queued|running|done|error), error_message, started_at,
  completed_at
```

`design_artifacts` (existing table) gains **no new column** — meeting recordings and transcripts are ingested as a **new `artifact_type`** value (`meeting_transcript`, `meeting_recording`), reusing the existing dedupe-by-sha256 Memory Bank machinery for free. Design/Figma inputs reuse the existing `figma_png` type as-is.

A rewrite's "existing SOW" input, if it's an uploaded file rather than one of this platform's own generated documents, is ingested as the existing `sow` artifact type and parsed via the unmodified `design_ingest.py` path to seed the ledger — no duplicate parser needed.

---

## 4. Backend services & Celery tasks

New service modules, mirroring existing naming/patterns:

- `app/services/sow_ledger.py` — Pass 1: source-to-ledger extraction (transcript text call via `llm_router`, delegates video/audio to a small extension of `video_ingest.py`, delegates design frames to a small extension of `figma_service.py`/`design_ingest.py` for the UI Element Inventory prompt specifically).
- `app/services/sow_drafting.py` — Pass 2: grouping + per-section chunked generation.
- `app/services/sow_audit.py` — Pass 3: completeness verification, coverage scoring.
- `app/services/sow_assembly.py` — Pass 4: skeleton ordering + render-to-format (delegates actual file rendering to `sow_export.py`).
- `app/services/sow_export.py` — `.md` (trivial, source of truth), `.docx` (via `python-docx` — new dependency, pure-Python, no system package needed, adds cleanly to `requirements.txt`), `.pdf` (recommend `weasyprint` rendering the same content from HTML/CSS — more control over a professional SOW layout than `reportlab`'s canvas API; flag: `weasyprint` needs system Pango/Cairo libs, so it must be added to `docker/Dockerfile.backend`, not just `requirements.txt` — same category of change as the `ffmpeg` addition already done for video stills).
- `app/services/sow_patch.py` — rewrite/patch orchestration: given a document + new source input + optionally a set of target `section_key`s, re-runs Pass 1 scoped to the new source, re-drafts only the targeted sections (or all, for full regen), re-runs Pass 3 on changed sections only, and writes a new `sow_document_versions` row with `kind=patch` and `parent_version_id` set — old version is never mutated, matching the platform's existing audit-trail discipline (`audit_log.py`, `visual_runs` history).

Celery tasks in `app/workers/tasks/sow_generation.py`:
- `generate_sow_task(document_id, version_id)` — runs Pass 1→4 for a full generation, updating `sow_generation_jobs.stage`/`stage_progress` as it goes (same "long job, poll or stream progress" shape as `ai_execution.py`/`visual_audit.py` tasks already use).
- `patch_sow_task(document_id, version_id, target_sections|null)` — the rewrite path.

Progress is exposed to the frontend either via the existing SSE pattern (`ai-testing/page.tsx` already has a working, now-fixed token-auth'd SSE client — `sow` page reuses `getAccessToken()` from `lib/api.ts`, learned from the 2026-07-16 SSE bug already fixed there) or simple polling of `sow_generation_jobs` — SSE recommended for consistency with the rest of the platform and because generation of a huge SOW is exactly the kind of multi-minute job where silent waiting is bad UX.

## 5. API surface (`backend/app/api/v1/sow.py`, new router file)

All routes gated by `require_permission("sow")` (new permission key — kept separate from `vibe_testing` so an admin can grant SOW authoring without granting the AI-agent execution surface, or vice versa; this matches the existing principle that "access to each area is explicitly granted per user").

```
POST   /api/v1/sow/documents                     create a new SOW document (title, project_id)
POST   /api/v1/sow/documents/{id}/sources         upload a source (transcript text | recording
                                                   file | design image) — stores as design_artifacts
POST   /api/v1/sow/documents/{id}/generate        kick off full generation (Celery)
GET    /api/v1/sow/documents/{id}/generation      poll job status (or SSE variant)
GET    /api/v1/sow/documents                      list (filter by project, status)
GET    /api/v1/sow/documents/{id}                 current version + sections + coverage
GET    /api/v1/sow/documents/{id}/versions        version history
GET    /api/v1/sow/documents/{id}/versions/{vid}  a specific version (for diff/rollback view)
PATCH  /api/v1/sow/documents/{id}/sections/{key}  manual hand-edit of a section's content_blocks
POST   /api/v1/sow/documents/{id}/rewrite         patch or full-regen (kind, target_sections?,
                                                   new source ids)
POST   /api/v1/sow/documents/{id}/export          {format: md|docx|pdf} → returns a download URL
POST   /api/v1/sow/documents/{id}/send-to-checkpoints  wraps current version as a `sow` artifact
                                                   and calls the existing design_ingest pipeline —
                                                   zero duplicate logic, reuses it as-is
DELETE /api/v1/sow/documents/{id}                 soft delete
```

---

## 6. Frontend

**Nav:** new entry in `AppShell.jsx`'s `NAV` array — `{ label: "SOW", href: "/sow", permission: "sow", icon: <FileText size={16} /> }` (lucide-react, already an installed dependency).

**Route:** `frontend/src/app/sow/page.tsx`, structured as tabs (matching the existing `/ai-testing` tab pattern):

- **New / Generate** tab — mirrors `SowCheckpointsSection.tsx`/`VisualAuditSection.tsx` upload UX: transcript text box or file upload, meeting recording uploader (reuses the "Platform / product name" required-field pattern already proven in Video Walkthrough for anti-hallucination grounding), design artifact uploader (reuses `FigmaImportSection.tsx`), then "Generate SOW" → progress view (reusing the SSE hook pattern) → lands in the editor.
- **Library** tab — table of SOW documents (status, coverage %, last updated, project), sortable, same bulk-action affordances already established in `SkillsTab.tsx` (checkboxes, bulk delete/reassign) for consistency.
- **Editor** (opened from Library or right after generation) — left-hand section outline (from `content_blocks`), main pane renders/edits the selected section, right-hand **Coverage panel** listing ledger rows with a covered/missing indicator per section (this is the "prove nothing was dropped" surface the stakeholder explicitly asked for) — clicking a missing item scrolls to where it was auto-appended for review. Toolbar actions: Save, Rewrite (opens a modal to add new sources + choose "these sections" vs "everything"), Export (format picker), Send to Vibe Testing.
- **Version history** — simple list with version number, kind (full/patch), date, "View" (opens a read-only diff of changed sections vs the version it patched — client-side text diff over each section's rendered content_blocks is sufficient; no need for a heavyweight diff service).

---

## 7. Reliability, security, and cost — non-negotiables carried over from the existing platform

- **No silent gaps.** Every pass either produces a result or a recorded `error_message` on `sow_generation_jobs`/`sow_document_versions` — never a partially-done document silently marked "ready." This directly satisfies "if anything goes missing... it will impact the business."
- **Schema validation on every LLM output**, entry by entry, invalid entries dropped-and-logged not guessed — same rule already enforced in `design_ingest.py::_validate_checkpoint`.
- **Traceability.** Every ledger row and every section keeps a `source_ref`, so any sentence in a generated SOW can be traced back to the transcript timestamp, video still, or design frame it came from — critical for a document used as a legal/contractual-adjacent QA baseline.
- **Dedup for free.** Reusing `design_artifacts` + sha256 means re-uploading the same meeting recording during a rewrite never re-costs a full re-transcription.
- **Secrets/config.** New env vars follow the existing `VISUAL_LLM_*` naming convention (e.g. `SOW_LLM_PRIMARY`, `SOW_LLM_FALLBACKS`) — no hardcoded keys, same `llm_router.py` chain.
- **Auth.** Every new endpoint behind `require_permission("sow")`; file uploads validated for type/size before hitting storage, matching existing upload endpoints.
- **Cost/latency control for huge projects.** Section-scoped chunking (not char-count chunking) keeps each drafting call well within token budget regardless of project size, same principle as `_CHUNK_MAX_CHARS` in `design_ingest.py`. Full generation of a large SOW is expected to take minutes, not seconds — this is why it's a Celery job with visible progress, not a synchronous request.
- **New system dependency risk:** `weasyprint` (PDF export) needs native libs in the Docker image — must be added to `docker/Dockerfile.backend` and verified in a container build, not just pip-installed locally. This is the one infra change in this plan that isn't "just Python."

---

## 8. Phased delivery

| Phase | Scope | Why this order |
|---|---|---|
| **0 — Foundations** ✅ 2026-07-20 | Migration (all new tables), `sow` permission key, nav entry + empty `/sow` page behind the permission gate, `sow.py` router skeleton (CRUD only, no generation yet) | Nothing else can be built/tested without the schema and access-control scaffolding existing first |
| **1 — Ingestion** ✅ 2026-07-20 | Transcript upload, meeting recording upload (extends `video_ingest.py`), design artifact reuse. Delivered more than originally scoped here — real LLM extraction with the exhaustive per-control prompting Phase 2 below was meant to formalize, not just "prove sources land." See README changelog for the full breakdown and what's still unverified (no live DB/worker/Gemini call in the sandbox this was built in). | Validates the hardest new integration (audio/video → ledger) in isolation before layering generation logic on top |
| **2 — Ledger + UI Inventory extraction** 🔶 partially done | Real prompting/validation/traceability (`source_ref`) landed in Phase 1. Still open: validation against a *real* meeting recording/screenshot in your actual environment (not just import/schema checks), and the "constrained regrouping against the previous version's sections" refinement — moot until Phase 5 versioning exists to regroup against. | This is the pass the whole completeness guarantee depends on — needs its own validation pass against a real recording before Pass 2 is built on top of it |
| **3 — Drafting + Assembly (full generation only)** ✅ 2026-07-20 | Pass 2 + 4, no audit pass yet, no editor yet — generate a full SOW end-to-end, viewable as read-only rendered markdown. Delivered: `sow_drafting.py` (grouping + per-section drafting with the ui_element completeness safety net), `sow_assembly.py` (LLM overview/scope + 5 never-LLM-drafted templated trailing sections), `generate_sow_task` (partial-failure model — a `done_with_errors` version keeps every section that succeeded), the `generate`/`generation`/`versions`/`versions/{id}` API, and a `/sow/[id]` frontend view with a Generate button, job polling, and a read-only per-section rendered view with status badges. See README changelog for two bugs caught and fixed during self-review (a split-commit stuck-'generating' risk, and a Celery `task_acks_late` redelivery duplication risk) and what's still unverified (no live DB/worker/Gemini call in the sandbox this was built in). | First point a stakeholder can actually read a generated SOW and judge quality |
| **4 — Completeness audit + coverage UI** ✅ 2026-07-20 | Pass 3, coverage scoring, the editor's Coverage panel. Delivered: `sow_audit.py` (independent per-section LLM audit — deliberately not the same call that drafted it — judging fact-by-fact whether the draft actually represents each assigned ledger row; any fact the audit response doesn't address is treated as a gap, never assumed covered by omission), wired into `generate_sow_task` as its own job stage between drafting and assembly, `coverage_score`/`coverage_gaps` persisted per section (both columns already existed from the Phase 0 migration — no new migration needed) and surfaced in the existing `/sow/[id]` read-only view as a color-coded badge plus a listed gaps panel. No in-app editor exists yet (Phase 5), so "the editor's Coverage panel" from this row's original scope is the read-only view's coverage badge/gaps panel instead — noted explicitly as a scope decision, not a shortfall, since Phase 5's editor will reuse the same `coverage_score`/`coverage_gaps` fields once it exists. Scoped to functional sections only — Overview/Scope/templated trailing sections stay `coverage_score=null` permanently (narrative by design, not exhaustive checklists), documented in `sow_audit.py`'s module docstring. | This is what turns "an AI wrote a document" into "a document proven not to have dropped things" — the stakeholder's hard requirement |
| **5 — Editor + versions** ✅ 2026-07-20 | Structured editable sections, save, version history/diff view. Delivered: `PATCH .../documents/{id}/sections/{key}` (always targets `current_version_id` — editing an older version isn't supported yet, avoiding ambiguity without full patch/rewrite machinery; re-validates every block server-side via the same `_validate_block` LLM output goes through; sets `edited_by_human`/`edited_at`, clears the now-stale `coverage_score`/`coverage_gaps`; promotes a section — and the version/document, if it was the last one — out of `error` on a successful fix). A structured per-block-type editor in `/sow/[id]` (heading/paragraph/control_spec/bullet_list/table/callout, each with type-appropriate fields, reorder, delete, add) rather than a raw markdown box, so every hand-edit is exactly as schema-valid as an LLM draft. A client-side line diff (no diff service, per plan) comparing the selected version against the one before it by `version_number`, matched by `section_key`, flagging added/removed/changed/unchanged sections. **Scope note:** `edited_by_human` protects nothing against a plain "Generate" yet — that still always creates a fresh version from scratch (Phase 3's design); the frontend warns before Generate if the current version has hand-edits, and the actual skip-regenerating-edited-sections behavior is Phase 7's job once rewrite/patch exists. | |
| **6 — Export + Send-to-Checkpoints** ✅ 2026-07-20 | `.md`/`.docx`/`.pdf` export, wraps-and-reuses `design_ingest.py` for the checkpoints hand-off. Delivered: `app/services/sow_export.py` (markdown/HTML/DOCX/PDF renderers, all walking the same typed `content_blocks` — DOCX via native `python-docx` objects, PDF via `weasyprint` rendering the same HTML the DOCX converter's logic mirrors, not markdown pasted into a document). `POST .../export` streams the file back directly, generated on demand, never persisted (plan §11.7). `POST .../send-to-checkpoints` reuses `visual_audit.py::upload_sow`'s exact artifact-creation contract (sha256 dedupe, same storage path, same `ingest_sow_task` enqueue) with rendered markdown standing in for an uploaded file — zero duplicated parsing logic. New deps: `python-docx==1.2.0`, `weasyprint==69.0` (+ system Pango/Cairo/GDK-pixbuf libs in `docker/Dockerfile.backend`, same category of change as the existing `ffmpeg` addition). All four renderers were actually exercised end-to-end in the build sandbox (not just import-checked) — see README changelog. | Deliberately last — reuses everything built above; lowest risk, highest visible payoff |
| **7 — Rewrite/patch** ✅ 2026-07-20 | `sow_patch.py`, target-section selection UI, patch vs full-regen choice. Delivered: `app/services/sow_patch.py` (`non_patchable_section_keys()` — framing + templated sections excluded, `filter_protected_sections()` — plan §11.4 hand-edit protection, `draft_and_audit_section()` — shared Pass2+Pass3 helper extracted from `generate_sow_task` so patch and full-generation can never drift on drafting/audit behavior). `POST .../documents/{id}/rewrite` creates a new `kind=patch` version whose `parent_version_id` points at the version it patched, copies every non-targeted section forward unchanged, and enqueues `patch_sow_task` to redraft only the targeted ones from their already-assigned ledger facts (`SowRequirementsLedger.assigned_section_key`, newly stamped during Pass 2a grouping in `generate_sow_task`). Full regeneration remains the unchanged `POST .../generate` — "patch vs full-regen" is simply which endpoint the frontend calls, not a modal choice. Frontend: a checkbox picker over the current version's patchable sections (mirroring `non_patchable_section_keys()`), an inline "hand-edited — force regenerate anyway?" override checkbox per protected section, and a "Rewrite N sections" button reusing the existing job-polling machinery from Phase 3/4. **Bug caught and fixed in self-review**: the rewrite endpoint originally validated `target_sections` against `current_version_id` before acquiring the row lock, leaving a race window where a concurrent `/generate` could move `current_version_id` before the lock was taken, making all prior validation stale relative to the version actually being patched. Fixed by locking first, then reading `current_version_id` and all downstream state from the locked row. Verified: py_compile, full app import (106 routes), OpenAPI schema regen, TestClient 401 sweep on `/rewrite` and `/generate` (rate-limit decorator didn't bypass auth), Celery task registration for `patch_sow_task`, frontend JSX syntax via babel. **Not verified**: no live Postgres/Celery worker/Docker container in this sandbox — the actual patch drafting flow, the copy-forward transaction under real concurrency, and the frontend panel's rendering were not exercised against a running stack. **Known limitation**: documents generated before this phase shipped won't have `assigned_section_key` stamped on their ledger facts, so `/rewrite` will find zero facts for every section until the document is fully regenerated once via `/generate`. | Needs a stable, versioned document model (Phases 0–6) to patch against |

Each phase ships independently testable and demoable — no phase requires guessing at a later phase's design.

---

## 9. Testing & verification strategy

- **Unit:** ledger schema validation, section-grouping logic, coverage-scoring math (given N ledger rows and a draft missing M, score = (N-M)/N), chunk-boundary logic (a feature group is never split mid-group even at max size).
- **Integration:** one fixture meeting recording + one fixture design screenshot + one fixture transcript run through the full Pass 1→4 pipeline in a test, asserting every UI element in a hand-authored "expected inventory" fixture appears in the final coverage report as covered (this is the regression guard against the exact "small UI elements get missed" failure class already seen once in this repo).
- **Manual QA gate before ship:** generate an SOW from a real recorded walkthrough of a real feature in this platform (e.g. record the Skills tab's bulk-action toolbar — buttons, checkboxes, sort dropdown — a good stress test since it's dense with exactly the control types called out as must-cover), and manually verify zero missed controls before Phase 4 is considered done.
- **Regression guard on existing features:** since `design_ingest.py` and `video_ingest.py` are extended/reused, not forked, re-run existing SOW Checkpoints and Video Walkthrough manual test flows after each phase touching those files to confirm no behavior change for their existing callers.

---

## 10. Open items — RESOLVED, see §11.1 (Hardening Addendum)

---

## 11. Hardening Addendum (v1.1)

Added after a deliberate second pass whose only job was to find what would break in production or under real use, before any code was written — per the standing instruction that this feature must not ship half-cooked. Everything below is now considered part of the plan, not optional polish.

### 11.1 Decisions locked (resolves the three open items from v1.0)

No response was given on the three open items, so implementation proceeds on the recommended defaults below. Each is cheap to revisit later — none of them are irreversible architecture — flag at any point and it gets changed.

1. **PDF export: `weasyprint`.** Adds a native-lib dependency to `docker/Dockerfile.backend` (same category of change as the existing `ffmpeg` addition for video stills — precedent already exists in this repo). Gets a genuinely professional layout from the same HTML/CSS the in-app editor already renders, instead of hand-coding a `reportlab` canvas.
2. **Meeting recording duration cap: 60 minutes**, rejected at upload with a clear 400 (matching the existing `_MAX_SOW_BYTES`/`_MAX_UPLOAD_BYTES` precedent in `visual_audit.py`). Most requirements-gathering meetings fit well inside this; a hard cap protects against a multi-hour recording silently burning the most expensive operation on the platform. Cap is an env var (`SOW_MAX_RECORDING_MINUTES`), not hardcoded, so it can be raised per-deployment without a code change.
3. **`sow` is a distinct permission key**, separate from `vibe_testing`. Confirmed by the existing precedent in `app/core/permissions.py` itself: every feature area gets its own grantable key, on purpose, so access can be handed out narrowly.

### 11.2 Idempotency & crash recovery for generation jobs

Celery workers die mid-job (this has already happened once in this codebase — `visual_qa_reconcile.py` exists specifically to un-stick `design_artifacts` rows left in `processing` by a dead worker). The SOW pipeline gets the same protection from day one, not bolted on after the first real incident:

- `sow_generation_jobs.updated_at` (`onupdate=func.now()`) is bumped on every stage transition.
- A new reconciliation task, `app/workers/tasks/sow_reconcile.py::reconcile_stuck_sow_jobs`, runs on Celery Beat (same schedule slot as the existing `visual_qa_reconcile` task) and marks any job still `running` with `updated_at` older than `SOW_JOB_STALE_MINUTES` (default 20) as `error` with a explicit `"worker died or lost contact"` message — never left silently spinning forever in the UI.
- Each pass (ledger/drafting/audit/assembly) is individually resumable: if a job errors after Pass 2 completes, retry re-enters at Pass 3 using the sections already written (`sow_sections.status = 'done'` rows are not re-drafted), not from scratch. This also caps retry cost.

### 11.3 Concurrency control

Two rewrites fired at the same document simultaneously must not race to write two divergent "next" versions. `sow_documents` gets a `status` guard enforced at the API layer: `POST .../generate` and `POST .../rewrite` both check-and-set `sow_documents.status` to `generating` inside the same transaction as creating the new `sow_document_versions` row (`SELECT ... FOR UPDATE` on the document row); a second request arriving while status is already `generating` gets a `409 Conflict` with the in-progress job id, not a silently-accepted duplicate job. This is a two-line guard, not a queueing system — sufficient because this is a low-frequency, human-triggered action, not a high-throughput API.

### 11.4 Stable section keys & human-edit protection

Two related risks: (a) LLM-assisted section grouping is not guaranteed to produce the same `section_key` set on every regeneration, which would silently break "patch this specific section" targeting; (b) a rewrite must never clobber a section a human already hand-edited.

- **Stable keys:** on any regeneration (full or patch) after version 1, Pass 2's grouping call is given the *previous* version's section list (`section_key` + `heading`) as a required alignment hint and instructed to map new content onto existing keys wherever the same feature/module is being described, only minting a new `section_key` for genuinely new scope. This is a constrained re-grouping, not free grouping — it is asked to reconcile against a fixed list, not invent one from nothing.
- **Human-edit protection:** `sow_sections` gains `edited_by_human: bool` (set the moment a `PATCH .../sections/{key}` call succeeds) and `edited_at`. `sow_patch.py` skips regenerating any section where `edited_by_human = true` **unless** the rewrite request explicitly includes that `section_key` in an `override_manual_edits` list — mirrors the exact rule already shipped for `ai_skills` manual edits (README, Skills tab: *"manual edits are protected from being overwritten by re-analysis"*). The Rewrite modal in the UI surfaces this: hand-edited sections are visibly flagged and excluded from "regenerate everything" by default.

### 11.5 Partial per-section failure model

A generation job is not one atomic pass/fail unit — with a large project split into many sections, one section's drafting call timing out must not discard every other section that succeeded. `sow_sections` gains a `status` column (`pending | generating | done | error`) and `error_message`. `sow_generation_jobs.status` only becomes `error` if **zero** sections completed; otherwise it becomes `done_with_errors` (new status value) and the document is usable with the failed sections clearly marked in the editor with a one-click "Retry this section" action that re-runs Pass 2+3 for just that `section_key`. This is the difference between "the AI SOW tool broke on my big project" and "everything came through except one section, which I retried in ten seconds" — the latter is the only acceptable behavior for a tool this plan explicitly says must never lose information silently.

### 11.6 `content_blocks` — concrete schema (was hand-waved in v1.0, now fixed)

Every section's content is a JSON array of typed blocks, `schema_version: 1`, validated with a pydantic model on write (both LLM-produced and hand-edited) so the editor, differ, and exporters can all rely on one contract instead of parsing arbitrary markdown:

```json
[
  { "type": "heading", "level": 2, "text": "Bulk Actions Toolbar" },
  { "type": "paragraph", "text": "Appears above the Skills table when one or more rows are checked." },
  { "type": "control_spec",
    "element_type": "checkbox", "label": "Row selector",
    "behavior": "Selects the row for bulk actions; a header checkbox selects all visible rows.",
    "ledger_ref": "ledger-row-uuid-1234" },
  { "type": "control_spec",
    "element_type": "dropdown", "label": "Sort by",
    "behavior": "Options: Name, Date added. Persists across reloads.",
    "ledger_ref": "ledger-row-uuid-1235" },
  { "type": "bullet_list", "items": ["Bulk delete", "Bulk project assignment", "Bulk run"] },
  { "type": "table", "headers": ["Field", "Required", "Notes"], "rows": [["Title", "yes", "..."]] },
  { "type": "callout", "tone": "warning", "text": "Auto-recovered: not explicitly described in drafted prose, added from the requirements ledger — verify." }
]
```

`control_spec` is the block type that makes the "every button/dropdown/checkbox/toggle/slider" requirement concrete and machine-checkable — it's a structured, coverage-auditable fact, not just a sentence hoping to mention the control. The completeness audit (Pass 3, §2) checks for a `control_spec` block per ledger `ui_element` row, not for a keyword match in prose — a strictly stronger and more reliable check than the v1.0 description implied.

### 11.7 Export storage & cleanup policy

`.docx`/`.pdf` are generated **on demand**, streamed back, and **not persisted** — the structured `content_blocks` is the single source of truth, and a stored export file would immediately go stale the moment a section is edited or patched, becoming a silent trust problem of exactly the kind this whole feature exists to prevent. `.md` export is likewise rendered on demand from the same source. No new cleanup/retention job is needed because nothing new is retained.

### 11.8 Rate limiting & abuse prevention

`POST .../generate` and `POST .../rewrite` are rate-limited via `slowapi` (already a dependency, already used elsewhere in this codebase) — default `SOW_GENERATE_RATE_LIMIT=10/hour` per user, configurable. This is the only endpoint class in this feature that can trigger multi-minute, multi-dollar LLM/video-API spend per call; every other endpoint (CRUD, section edit, export) is cheap and unlimited.

### 11.9 Project-scoped access control — corrected after checking the actual codebase

**Correction from the first draft of this addendum:** I initially wrote this section assuming `app/api/v1/projects.py` enforces per-user project membership and that SOW access should mirror it. I checked before implementing — **it doesn't**. This codebase has no `ProjectMember`/per-project-ACL concept anywhere; `list_projects` only requires `get_current_user`, and every existing project-scoped resource (`design_artifacts`, `visual_runs`, defects, test suites) is visible to anyone holding the relevant flat permission key, with `project_id` used purely as a filter/label, not an access boundary. Designing SOW access to be stricter than every other resource in the platform would be inconsistent, not more secure — it would just be a different, undocumented model nobody else expects.

**Actual rule implemented:** `sow_documents.project_id` is nullable and behaves exactly like `design_artifacts.project_id` — a convenience filter for the UI (list by project) with zero enforcement difference between project-scoped and org-wide documents. Anyone holding the `sow` permission can see and act on any SOW document, same as anyone holding `vibe_testing` can see any Visual QA artifact today. If per-project access boundaries are wanted later, that's a platform-wide change (a real `ProjectMember` model + enforcement across every existing resource), not something to bolt onto one new feature alone.

### 11.10 Task timeouts & heartbeats

Celery `generate_sow_task`/`patch_sow_task` get explicit `soft_time_limit`/`time_limit` (`SOW_TASK_SOFT_TIMEOUT_S` default 1800, hard timeout +120s grace) so a pathological hang (stuck provider call not honoring its own timeout) can't occupy a worker slot indefinitely — `llm_router.py`'s own per-call timeout is a second, inner layer of the same protection, not a substitute for it. `sow_generation_jobs.stage_progress` is updated after every individual section completes (not just at pass boundaries), so a long job always shows live movement in the UI rather than one opaque "generating…" spinner for 20 minutes — directly reuses the SSE plumbing already fixed for `ai-testing`/`execute` in the 2026-07-16 changelog entry.

### 11.11 Migration & testing rigor

- The Phase 0 migration follows the exact idempotent pattern already established in `0021_add_sow_parts.py` (`_table_exists`/`_column_exists` guards, explicit `downgrade()` for every `upgrade()`), not a bare Alembic autogenerate.
- Unit tests for Pass 1–4 logic run against a **mocked** `llm_router.call()` (deterministic fixture responses) so CI never spends real API budget and never flakes on provider latency — the one real-model integration test (§9) is manual-gate only, run once per phase before sign-off, never in CI.
- Every new Pydantic schema validates on both write paths (LLM output and human hand-edit via `PATCH .../sections/{key}`) — a hand-edit that produces malformed `content_blocks` is rejected with a 422, never silently saved as something the editor can't re-render.

### 11.12 Data model deltas vs. §3 (v1.0)

Superseding/additive to the original table definitions in §3:

- `sow_documents`: `+ status` gains value `generating` (concurrency guard, §11.3); add `updated_at`.
- `sow_document_versions`: `+ status` gains value `done_with_errors` (§11.5).
- `sow_sections`: `+ status` (`pending|generating|done|error`), `+ error_message`, `+ edited_by_human`, `+ edited_at`, `+ schema_version` on `content_blocks`.
- `sow_generation_jobs`: no structural change, semantics tightened per §11.2/§11.5.
- `sow_requirements_ledger`: no structural change from v1.0.

Everything above lands in the same Phase 0 migration (§8) — there is no reason to ship the weaker v1.0 schema first and migrate again immediately after.
