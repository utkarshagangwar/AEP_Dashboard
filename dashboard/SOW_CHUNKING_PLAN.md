# SOW Structure-Aware Chunking — Implementation Plan

**Status:** Proposed
**Owner:** Utkarsh Gangwar
**Supersedes:** `design_ingest.chunk_text()` paragraph-window chunking
**Related:** `SOW_FEATURE_PLAN.md` §2 (Pass 1), §8 (phasing), `Vibe_Test_Gaps_and_Implementation_Checklist.md`

---

## 0. Problem Statement — corrected

The original framing was *"the AI is not chunking, so at retrieval time the agent loses context and hallucinates."* Both halves of that are inaccurate, and the correction changes the design:

**Chunking already exists.** `app/services/design_ingest.py:188` `chunk_text()` splits on `\n\n` into ≤20,000-char windows. All three ingestion paths already call it:

| Caller | File | Line |
|---|---|---|
| SOW Checkpoints ingest | `app/workers/tasks/sow_ingest.py` | 220 |
| Transcript ledger | `app/services/sow_ledger.py` | 202 |
| Import SOW ledger | `app/services/sow_ledger.py` | 284 |

**There is no retrieval step.** No embeddings, no pgvector, no FAISS, no vector table. This is a **map/reduce full-sweep** pipeline: every chunk is sent to the LLM and results are concatenated. "Losing context at retrieval" is not the failure mode.

### The actual defects

| ID | Defect | Evidence | Business impact |
|---|---|---|---|
| **D1** | **Boundary loss.** A fixed 20k character window cuts mid-requirement, mid-table, mid-numbered-clause. The LLM sees a fragment with no idea which section it belongs to. | `chunk_text()` splits purely on `\n\n` and length. A `.docx` table serialised by `sow_import._extract_docx_text` becomes flat `a \| b \| c` lines with no table identity — a split mid-table loses the header row entirely. | Requirements split across a boundary are extracted as two half-facts or dropped. Directly violates the "exhaustiveness over brevity" contract in `_LEDGER_RULES`. |
| **D2** | **Zero positional context.** Each chunk gets only `"part 3 of 7"`. The model does not know the document title, the enclosing section, or what immediately preceded. | `sow_ledger.py:210`, `:292` | Model infers section context → invents `location` values, or leaves `location=null` where the heading made it obvious. Both degrade the ledger. |
| **D3** | **No cross-chunk dedup.** | `all_facts.extend(facts)` at `sow_ledger.py:207` and `:289`, with an explicit `# no cross-chunk dedup (Phase 1 scope)` comment. | A control described in three sections yields three ledger rows → three duplicate vibe tests, three duplicate audit entries, inflated coverage numbers. |
| **D4** | **Silent partial success.** If chunk 4 of 7 fails, the loop logs a warning, continues, and returns `success` with 6/7 of the facts. | `sow_ledger.py:210-214`, `:292-297`. `if not models_used: raise` — only raises if *every* chunk fails. | An incomplete ledger becomes the SOW baseline with no signal to the user. This is the exact "never a silent empty success" rule the module docstrings claim to enforce, unguarded for the *partial* case. |
| **D5** | **Silent fact truncation.** `_MAX_FACTS_PER_CALL = 200` slices the LLM's list without erroring. | `sow_ledger.py:_validate_facts` — `raw_items[:200]` | A dense screen returning 240 facts loses 40 with no log line distinguishing it from a genuine 200. |

D1 and D2 are what this plan calls "chunking." D3, D4, D5 are adjacent bugs in the same code path and are in scope because fixing chunking without them produces a cleaner pipeline that still emits duplicates and still lies about completeness.

---

## 1. Strategy Decision

### Options considered

| Option | How it works | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Structure-aware routing** (no new deps) | Route by file type: `.docx` → heading/table structure, `.md` → heading tree, `.pdf` → page + heading heuristic, transcript → speaker turn. Split only at structural boundaries. | Deterministic → unit-testable with exact assertions. Zero added LLM/embedding cost. No new dependencies. Preserves the exact information a semantic chunker is trying to recover, because the author already encoded it as headings. | Weak on unstructured walls of text (a `.txt` with no headings falls back to paragraph mode). | **CHOSEN** |
| B. Structure-aware + LLM boundary pass | A, then one LLM call per oversized section to pick split points. | Handles unstructured text. | Non-deterministic → cannot assert exact boundaries in tests, only invariants. +1 LLM call per oversized section. Adds a failure mode to a pipeline that already has D4. | Deferred to Phase 4, gated on real-world evidence that fallback-mode documents are a problem. |
| C. Embedding-based semantic chunking | Embed sentences, split at cosine-similarity troughs. | True "semantic chunking." | New dependency + embedding API cost per document. Non-deterministic. Slowest. **And it solves a retrieval problem this pipeline does not have** — there is no top-k selection step for better boundaries to improve. | Rejected. |
| D. Agentic chunking | An LLM agent reads the document and decides chunk boundaries + summaries iteratively. | Highest theoretical fidelity. | O(n) LLM calls, high cost/latency, deeply non-deterministic, near-impossible to TDD. | Rejected for this codebase's cost profile. |

**Recommendation: Option A.** The reason "semantic chunking" outperforms naive chunking in published benchmarks is that it recovers document structure that the naive splitter destroyed. In a `.docx` or `.md` SOW, that structure is *already explicit* — heading levels, table boundaries, numbered clauses. Reading it directly is strictly more accurate than inferring it from embeddings, and it is deterministic, which is a hard requirement given the TDD/vibe-testing deliverable this feeds.

The document types where structure is genuinely absent (a flat `.txt` transcript dump) get a purpose-built speaker-turn strategy, not an embedding model.

### Scope confirmed
- **All three pipelines** share the new chunker.
- **Cross-chunk dedup: in scope.**
- **Partial chunk failure: fail the job.**

---

## 2. Target Architecture

### 2.1 New module — `app/services/doc_chunking.py`

Single owner of all chunking. `design_ingest.chunk_text()` is retained as a thin deprecated shim delegating to the paragraph strategy, so nothing outside this plan breaks.

```python
@dataclass(frozen=True)
class Chunk:
    index: int                  # 1-based
    total: int
    text: str                   # extractable content ONLY
    heading_path: list[str]     # ["4. Functional Requirements", "4.3 Candidate Management"]
    locator: str                # "p.12", "00:14:32", "§4.3.2" — traceability
    strategy: str               # "heading_tree" | "docx_structure" | ... | "hard_split"
    char_count: int
    context_header: str         # rendered breadcrumb prepended at prompt time
```

**Public API:**

```python
def chunk_document(
    text: str | list[Block],
    *,
    file_name: str,
    doc_kind: Literal["sow_document", "transcript", "checkpoints_sow"],
    document_title: str | None = None,
    max_chars: int = 20_000,
) -> list[Chunk]
```

### 2.2 Strategy routing matrix

| Input | `doc_kind` | Extension | Strategy | Atomic unit (never split) | Boundary preference |
|---|---|---|---|---|---|
| Existing SOW | `sow_document` | `.docx` | `docx_structure` | table (whole), list block, numbered clause | H1 > H2 > H3 > paragraph |
| Existing SOW | `sow_document` | `.md` | `heading_tree` | fenced code block, table block, list block | `#` > `##` > `###` > `\n\n` |
| Existing SOW | `sow_document` | `.pdf` | `page_heading` | page, detected clause | page break > detected heading > `\n\n` |
| Existing SOW | `sow_document` | `.txt` | `heading_tree` → `paragraph` fallback | — | detected heading > `\n\n` |
| Transcript | `transcript` | any | `speaker_turn` | one speaker turn | speaker change > timestamp block > `\n\n` |
| Checkpoints SOW | `checkpoints_sow` | `.txt/.md/.pdf` | as above | — | as above |
| Anything unmatched | — | — | `paragraph` (today's behaviour, unchanged) | — | `\n\n` |

**Hard rules, enforced in every strategy:**

1. **Never split inside a table.** A table exceeding `max_chars` is split on row boundaries with its header row **repeated** at the top of each continuation chunk.
2. **Never split inside a numbered/lettered clause block** (`4.3.2`, `(a)`, `REQ-014`).
3. **Never split inside a fenced code block.**
4. An atomic unit that alone exceeds `max_chars` is hard-split — but tagged `strategy="hard_split"`, logged at **WARNING** with the heading path, and surfaced on the part record. Degradation is observable, never silent.
5. **Every character of the input appears in exactly one chunk's `text`.** Overlap lives only in `context_header`, never in `text`. This is a test invariant (see T-C-001).

### 2.3 The context header — the actual anti-hallucination fix

Replacing `"part 3 of 7"` with a structured, explicitly-delimited breadcrumb:

```
<document_context>
Document: Acme Candidate Portal — Statement of Work v2
Section path: 4. Functional Requirements > 4.3 Candidate Management > 4.3.2 Bulk Actions
Part 3 of 7 (characters 41,200–61,000 of 138,400)
Locator: §4.3.2 / p.12
</document_context>

<preceding_context reason="continuity only">
DO NOT extract facts from this block. It is the tail of the previous part,
provided only so you can resolve references like "the button above" or
"this dropdown". Every fact you extract must come from <content> below.
...last 500 characters of the previous chunk...
</preceding_context>

<content>
...the chunk's actual text...
</content>
```

This addresses D1 and D2 together and, critically, it addresses them **without** introducing overlap-driven duplication — the overlap is present for reference resolution but explicitly fenced off from extraction. That instruction is itself a test target (T-P-004).

`heading_path` also becomes the default `location` hint for `ui_element` facts, cutting the model's need to guess.

### 2.4 Deduplication — `app/services/ledger_dedup.py`

Deterministic, no LLM, no fuzzy matching.

```python
def dedupe_facts(facts: list[dict]) -> tuple[list[dict], int]
```

**Merge key:** `(fact_type, element_type, normalize(label))`

`normalize()`: casefold → collapse internal whitespace → strip surrounding punctuation → strip leading articles (`the`, `a`, `an`) → strip trailing role nouns (`button`, `dropdown`, `menu`) **only when `element_type` already encodes it**.

**Merge rules on collision:**

| Field | Rule |
|---|---|
| `label` | keep the longest (most specific) surface form |
| `location` | first non-null; if two differ, keep both `"Header; Candidate list"` (a control on two pages is genuinely two placements) |
| `behavior_notes` | keep the longest; if the shorter contains sentences absent from the longer, append them |
| `source_ref` | join distinct refs with `"; "`, truncate at 500 chars (column limit), preserving the first 3 |

**Explicitly out of scope:** fuzzy/semantic near-duplicate matching. `"Delete"` and `"Delete selected"` will both survive. This is stated so the limitation is a known, documented gap rather than a surprise — Phase 4.

**Application point:** inside `extract_ledger_from_transcript()` / `extract_ledger_from_sow_document_full()`, before return. The Celery task layer (`workers/tasks/sow_ledger.py`) is untouched. **Cross-source dedup** (same control found in both a transcript and a design image) remains out of scope — it belongs at the task/document layer, Phase 2 of the parent plan.

### 2.5 Fail-the-job semantics

**D4 fix** — `extract_ledger_from_*_full()`:

```
for each chunk:
    attempt up to 3 times with exponential backoff (1s, 4s) on IngestError
    on final failure: record (chunk.index, chunk.heading_path, error)

if any chunk failed:
    raise IngestError(
        "Extraction failed for part(s) 4, 6 of 7 (sections: '4.3 Candidate "
        "Management', '5.1 Reporting') after 3 attempts each: <reason>. "
        "No facts were saved — retry this source."
    )
```

The task layer already maps `IngestError` → `SowSourceStatus.error` + `error_message`, so **no model or migration change is needed** for this. The partially-extracted facts are discarded rather than written; `_clear_prior_facts` already handles the retry case.

**D5 fix** — `_validate_facts()` gains `on_overflow: Literal["raise", "truncate"] = "raise"`.

> ⚠️ **Side effect:** the recording and image extraction paths also call `_validate_facts`. A genuinely dense screenshot returning >200 facts would now fail the source instead of silently truncating. This is the intended behaviour (silent truncation is D5), but it changes existing behaviour for those two paths. Regression check R-4 covers it.

### 2.6 Structured extraction — the prerequisite change

Structure-aware `.docx` chunking is impossible against a flattened string. `sow_import._extract_docx_text()` currently discards `para.style.name` (which carries `Heading 1`…`Heading 9`) and collapses tables to `a | b | c` lines.

**Add** `sow_import.extract_existing_sow_blocks(storage_path, file_name) -> list[Block]`:

```python
Block = dict  # {"kind": "heading", "level": 1..9, "text": str}
             # {"kind": "paragraph", "text": str}
             # {"kind": "table", "header": [str], "rows": [[str]]}
             # {"kind": "page_break", "page": int}    # pdf only
             # {"kind": "list_item", "level": int, "text": str}
```

**Keep** `extract_existing_sow_text()` as a thin renderer over `extract_existing_sow_blocks()` producing byte-identical output to today. This preserves backward compatibility and gives a free characterisation test (T-E-001).

`design_ingest.extract_text()` gets the same treatment for `.pdf` (emit `page_break` blocks) and `.md` (parse ATX/setext headings).

### 2.7 Schema change — migration `0038_chunk_context`

`sow_parts` (`app/models/visual_qa.py:120`) — all columns **nullable**, no backfill, existing rows unaffected:

| Column | Type | Purpose |
|---|---|---|
| `heading_path` | `Text` | JSON array of the section breadcrumb |
| `locator` | `String(200)` | `"p.12"` / `"§4.3.2"` / `"00:14:32"` |
| `strategy` | `String(40)` | which strategy produced this part; `"hard_split"` is the degradation signal |
| `context_header` | `Text` | the exact header sent to the LLM — reproducibility for debugging |

No change to `sow_requirements_ledger`. No change to `SowSourceStatus`.

---

## 3. Phased Delivery — TDD

Each phase is **red → green → refactor**. Tests are written first. Test IDs are stable and map 1:1 to the Robot Framework suites in §5.

### Phase 1 — Structured extraction (no behaviour change)
**Goal:** get structure out of the files without changing any output yet.

| Test ID | Assertion |
|---|---|
| T-E-001 | `extract_existing_sow_text()` output is **byte-identical** to the pre-change implementation for all 6 golden fixtures. *(characterisation test — written against the current code first)* |
| T-E-002 | `.docx` with H1/H2/H3 → blocks carry correct `level` for each heading |
| T-E-003 | `.docx` table → one `{"kind":"table"}` block with `header` separated from `rows`; cell count preserved |
| T-E-004 | `.md` ATX (`## X`) and setext (`X\n---`) both → `heading` blocks at correct level |
| T-E-005 | `.pdf` → `page_break` block between each page; page numbers 1-based and contiguous |
| T-E-006 | Empty/whitespace-only paragraphs are dropped, matching current behaviour |
| T-E-007 | Corrupt `.docx` → `IngestError` with user-safe message, no traceback leak |

**Exit criteria:** T-E-001 green (proves zero regression), all extraction tests green, no caller changed.

### Phase 2 — The chunker
**Goal:** `doc_chunking.py` correct in isolation. Still not wired in.

| Test ID | Assertion |
|---|---|
| **T-C-001** | **Lossless invariant.** `"".join(c.text for c in chunks)` reconstructs the input modulo normalised whitespace, for every strategy and every fixture. *Property test: 200 generated documents.* |
| T-C-002 | Document under `max_chars` → exactly one chunk, `strategy` reflects the file type, `text` unmodified |
| T-C-003 | `heading_tree`: splits occur at heading boundaries; no chunk starts mid-paragraph |
| T-C-004 | `heading_tree`: prefers the **shallowest** available heading level that satisfies `max_chars` |
| **T-C-005** | **No chunk exceeds `max_chars`** unless `strategy == "hard_split"` |
| **T-C-006** | **Table integrity.** A 15,000-char table inside an 18,000-char section is never split. |
| **T-C-007** | **Table continuation.** A 30,000-char table splits on row boundaries; **every** continuation chunk repeats the header row; no row is split across chunks. |
| T-C-008 | Numbered clause `4.3.2` spanning a natural boundary stays whole |
| T-C-009 | Fenced code block is never split |
| T-C-010 | `heading_path` is correct and hierarchical for every chunk (H1 > H2 > H3, no skipped ancestors) |
| T-C-011 | `speaker_turn`: split occurs at speaker changes, never mid-turn |
| T-C-012 | `speaker_turn`: `locator` is the first timestamp in the chunk, or `null` if untimestamped |
| T-C-013 | `page_heading`: `locator` is `"p.N"` matching the first page in the chunk |
| **T-C-014** | **Hard-split is flagged.** A single 60,000-char paragraph → `strategy == "hard_split"` on every resulting chunk **and** a WARNING log containing the heading path |
| T-C-015 | Unknown file type → `paragraph` strategy, output **identical** to legacy `chunk_text()` |
| T-C-016 | `index` is 1-based, contiguous, `total` equal across all chunks |
| T-C-017 | Empty input → `IngestError`, never `[]` |
| T-C-018 | Single trailing newline / CRLF / BOM does not change chunk count |

**Exit criteria:** T-C-001 and T-C-005 green under property testing. All strategies green.

### Phase 3 — Prompt assembly + wiring
**Goal:** context headers reach the LLM; all three callers migrated.

| Test ID | Assertion |
|---|---|
| T-P-001 | `context_header` contains document title, full section path, part N of M, char range, locator |
| T-P-002 | `<preceding_context>` = last 500 chars of chunk N-1; absent for chunk 1 |
| T-P-003 | `<preceding_context>` content never appears in `chunk.text` (no extraction overlap) |
| **T-P-004** | **Golden-set prompt test.** Given a fixture where a fact appears *only* in the preceding-context tail, the model returns **zero** facts sourced from it. *(runs against the live router in `golden_tests/`)* |
| T-P-005 | `heading_path` is passed as the `location` default hint for `ui_element` facts |
| T-P-006 | `extract_ledger_from_sow_document_full` calls `chunk_document(doc_kind="sow_document")` with the real `file_name` |
| T-P-007 | `extract_ledger_from_transcript` calls it with `doc_kind="transcript"` |
| T-P-008 | `sow_ingest` persists `heading_path`, `locator`, `strategy`, `context_header` onto each `SowPart` |
| T-P-009 | `design_ingest.chunk_text()` shim returns exactly its legacy output (deprecation safety) |

### Phase 4 — Dedup + failure semantics

| Test ID | Assertion |
|---|---|
| T-D-001 | Identical `(fact_type, element_type, label)` across two chunks → one row, `source_ref` joined |
| T-D-002 | `"Delete"` vs `"the Delete "` vs `"DELETE"` → one row; surviving `label` is the longest form |
| T-D-003 | Same label, **different** `element_type` → **two** rows (not merged) |
| T-D-004 | Same label, different `location` → one row, both locations preserved |
| T-D-005 | Merged row keeps the **longest** `behavior_notes`; unique sentences from the shorter are appended |
| T-D-006 | `source_ref` join truncates at 500 chars, first 3 refs preserved |
| T-D-007 | Dedup is order-independent: `dedupe(a+b) == dedupe(b+a)` as a set |
| T-D-008 | Dedup count is returned and logged at INFO |
| **T-F-001** | **One chunk fails all 3 attempts → `IngestError` raised**, message names the failing part numbers and their section headings |
| T-F-002 | Chunk fails twice, succeeds on attempt 3 → job succeeds, no error, retry logged at WARNING |
| T-F-003 | On job failure, **zero** ledger rows are written |
| T-F-004 | `_validate_facts` with 240 items and `on_overflow="raise"` → `IngestError` naming the count |
| T-F-005 | `on_overflow="truncate"` preserves legacy behaviour (kept for any caller that needs it) |
| T-F-006 | Task layer maps the new `IngestError` → `SowSourceStatus.error` with the message intact |

### Phase 5 — Golden set / end-to-end

| Test ID | Assertion |
|---|---|
| T-G-001 | 40-page `.docx` SOW: **100%** of manually-catalogued `ui_element` facts appear in the ledger (baseline captured against current code first, to prove improvement) |
| T-G-002 | Same document: duplicate ledger rows reduced vs. baseline |
| T-G-003 | No fact has `location=null` where its enclosing heading was unambiguous |
| T-G-004 | Every fact's `source_ref` resolves to a real locator in the source |
| T-G-005 | Total LLM call count within ±20% of baseline (chunking must not blow up cost) |

---

## 4. File Manifest

| File | Change |
|---|---|
| `app/services/doc_chunking.py` | **NEW** — `Chunk`, `chunk_document()`, all strategies, context-header rendering |
| `app/services/ledger_dedup.py` | **NEW** — `dedupe_facts()`, `normalize()` |
| `app/services/sow_import.py` | Add `extract_existing_sow_blocks()`; `extract_existing_sow_text()` becomes a renderer over it |
| `app/services/design_ingest.py` | `extract_text()` → block-aware for `.pdf`/`.md`; `chunk_text()` → deprecated shim |
| `app/services/sow_ledger.py` | Both `*_full()` fns: new chunker, retry loop, fail-on-partial, dedup. `_validate_facts` gains `on_overflow`. Both `extract_ledger_from_*` fns take a `Chunk` instead of `(text, part_label)` |
| `app/workers/tasks/sow_ingest.py` | Persist chunk metadata onto `SowPart` |
| `app/models/visual_qa.py` | `SowPart` + 4 nullable columns |
| `alembic/versions/0038_chunk_context.py` | **NEW** |
| `backend/tests/test_doc_chunking.py` | **NEW** — T-C-* |
| `backend/tests/test_sow_import_blocks.py` | **NEW** — T-E-* |
| `backend/tests/test_ledger_dedup.py` | **NEW** — T-D-* |
| `backend/tests/test_sow_ledger_failure.py` | **NEW** — T-F-* |
| `backend/golden_tests/references/chunking/` | **NEW** — 6 fixtures (see below) |

**Golden fixtures:** `structured.docx` (H1-H3 + 2 tables), `flat.txt` (no headings), `numbered.md` (clause-numbered), `scanned_text.pdf` (multi-page), `meeting.txt` (speaker-turn transcript), `pathological.txt` (one 60k-char paragraph → forces hard-split).

---

## 5. Feeding Vibe Testing

The test IDs above are the contract. Each maps to a Robot Framework suite so the same assertion exists at both the unit and the vibe-test layer.

```
backend/robot/sow_chunking/
├── resources/
│   ├── chunking_keywords.resource     # Upload SOW And Wait For Parse
│   │                                  # Get Ledger Facts For Document
│   │                                  # Ledger Should Contain UI Element
│   │                                  # Ledger Should Have No Duplicate Facts
│   │                                  # Part ${n} Should Have Heading Path
│   └── locators.resource              # data-testid first, no absolute XPath
├── variables/
│   └── fixtures.py                    # fixture paths + expected fact catalogues
└── suites/
    ├── 01_upload_and_chunk.robot      # T-C-002, T-C-005, T-C-016
    ├── 02_structure_preserved.robot   # T-C-006, T-C-007, T-C-010, T-P-008
    ├── 03_ledger_completeness.robot   # T-G-001, T-G-003
    ├── 04_dedup.robot                 # T-D-001..008 via the API
    └── 05_failure_surfacing.robot     # T-F-001, T-F-006 — error text visible in UI
```

Two things the implementation must expose for this to be testable at all — call these out during build, not after:

1. **`GET /api/v1/sow/documents/{id}/parts`** must return `heading_path`, `strategy`, `locator` per part. Without it suite 02 can only assert via direct DB access, which is not a vibe test.
2. **The chunking degradation signal must be visible in the UI.** `strategy == "hard_split"` on any part needs a `data-testid="part-degraded-badge"` element. Suite 05 asserts on it. A warning that only exists in worker logs cannot be vibe-tested.

---

## 6. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Changing the shared chunker regresses the already-shipped SOW Checkpoints pipeline | **HIGH** | T-C-015 + T-P-009 pin legacy paragraph behaviour byte-for-byte. Unknown file types keep the old path. Ship Phase 1–2 behind `SOW_CHUNKING_V2=false`, flip per environment. |
| Structure-aware chunks are *smaller* → more LLM calls → higher cost | MEDIUM | T-G-005 caps call-count drift at ±20%. Strategies pack sibling sections up to `max_chars` rather than one-chunk-per-heading. |
| Fail-the-job makes a previously "working" import start failing | MEDIUM | This is the point — those imports were producing incomplete ledgers. Error message must name the exact failing parts so the retry is actionable. Monitor error rate for one week post-deploy. |
| `on_overflow="raise"` breaks the recording/image paths | MEDIUM | Regression check R-4. If dense screenshots legitimately exceed 200 facts, raise the ceiling — do **not** revert to silent truncation. |
| Dedup merges two genuinely distinct controls sharing a label | MEDIUM | `element_type` is part of the merge key (T-D-003); differing `location` is preserved, not collapsed (T-D-004). Deterministic normalisation only — no fuzzy matching, which is where false merges come from. |
| `python-docx` heading detection fails on custom Word styles | LOW | Fall back to a heuristic (bold + short + standalone paragraph). Log at INFO when the fallback triggers so real-world frequency is measurable. |
| `sow_parts` rows created under the old chunker coexist with new ones | LOW | New columns are nullable. Parts are created once at ingest and never re-chunked. Existing artifacts are unaffected; re-ingest is the migration path. |

**Rollback:** `SOW_CHUNKING_V2=false` reverts routing to the legacy paragraph strategy. Migration 0038 is additive-nullable and does not need reverting.

### Regression checklist (post-merge)
- **R-1** Upload a small `.md` SOW to SOW Checkpoints → identical checkpoint count to pre-change
- **R-2** Upload a large `.pdf` → same or better part count, all parts analysable
- **R-3** Import an existing `.docx` SOW → ledger fact count ≥ baseline, duplicates < baseline
- **R-4** Upload a dense design screenshot → still succeeds; does not trip the new `on_overflow` raise
- **R-5** Upload a meeting recording → unaffected (does not go through the chunker)
- **R-6** Retry a previously-errored source → prior facts cleared, no duplication

---

## 6a. Implementation Record

**Status: Phases 1–4 implemented. 119 unit tests passing.**

### Deviations from the plan as written

| # | Planned | Actual | Why |
|---|---|---|---|
| 1 | Block extraction inside `sow_import.py` + `design_ingest.py` | One shared module `app/services/doc_blocks.py`; both delegate | Both needed the same block model and the same `.pdf`/`.md` parsers. Splitting would have duplicated the parsers or created an import cycle. |
| 2 | `IngestError` stays in `design_ingest` | Moved to `doc_blocks`, **re-exported** from `design_ingest` as the same class object | `doc_blocks` must not import `design_ingest` (the dependency runs the other way). Every existing `except design_ingest.IngestError` still works — asserted by `test_ingest_error_identity_preserved_across_modules`. |
| 3 | **T-E-001: byte-identical `extract_existing_sow_text()`** | **Retired.** Replaced by T-E-001a (content equivalence) + T-E-001b (ordering is fixed) | The old output was a bug: `.docx` tables were emitted after all paragraphs. Byte equality would have pinned that permanently. Approved mid-implementation. |
| 4 | `extract_text()` rendered from blocks | **Reverted** — keeps its original implementation | Rendering is lossy for that caller: it strips markdown `#` markers and list bullets, so the shipped SOW Checkpoints prompt would have lost the structure it currently sees. A cleanliness refactor is not worth degrading a live prompt. Guarded by `test_extract_text_is_byte_identical_to_legacy`. |
| 5 | — | Added `max_chars` parameter to both `extract_ledger_from_*_full()` | No way to tune chunk size (plan §7 Q1) or to test multi-chunk behaviour without it. |
| 6 | — | Split `_EXCERPT_RULE` into excerpt + location rules | The section-path-as-`location` hint applies to any chunk that knows its section, including a single-chunk document — which is the common case for a small SOW and exactly where a null `location` is most avoidable. |
| 7 | — | `PartOut` API extended with `heading_path`/`locator`/`strategy`/`degraded` | Required by plan §5. Without it the Robot suites could only verify chunking via direct DB access. |

### Defects found during implementation (beyond D1–D5)

| ID | Severity | Defect | Status |
|---|---|---|---|
| **D6** | **HIGH** | `.docx` document order destroyed — `_extract_docx_text()` read all paragraphs then all tables, relocating every table to the end of the document. A requirements table under §2 was emitted after §5, severing it from its heading. On any document over one chunk, all tables landed together in the final chunk. | **Fixed** (`_iter_docx_block_items` walks body XML in true order) |
| **D7** | MEDIUM | `.pdf` page boundaries erased — pages joined with `"\n\n"`, indistinguishable from a paragraph break, so a `p.12` locator was unrecoverable. | **Fixed** (explicit `page_break` blocks) |
| **D8** | MEDIUM | Numbered headings in `.txt`/`.pdf` (`"1. Project Overview"`) matched the list-item regex and were classified as list items, producing zero sections. | **Fixed** (heading detection ordered before list detection) |

### Test inventory

| File | Tests | Covers |
|---|---|---|
| `tests/test_doc_blocks.py` | 27 | T-E-001a/b, T-E-002…007, D6/D7/D8 regression guards |
| `tests/test_extraction_delegates.py` | 11 | `extract_text` byte-parity, extension gates, `IngestError` identity |
| `tests/test_doc_chunking.py` | 31 | T-C-001…018 (2 property-based via hypothesis) |
| `tests/test_ledger_dedup.py` | 32 | T-D-001…008 |
| `tests/test_sow_ledger_chunking.py` | 18 | T-P-001…009, T-F-001…005 |

Run with `cd backend && pip install -r requirements-dev.txt && pytest`.

### Not yet done

- **T-P-004 live half** — whether the model actually obeys the `<preceding_context>` do-not-extract fence needs a real LLM call. The unit half (the instruction is sent, and only for multi-chunk documents) is covered; the behavioural half belongs in `golden_tests/`.
- **T-G-001…005** — the golden-set baseline comparison needs a real 40-page customer SOW and a manually catalogued fact list.
- **Robot Framework suites** (§5) — the API now exposes what they need; the suites themselves are unwritten.
- **UI badge** for `degraded` parts — the API field exists, the frontend does not render it yet.

### ⚠️ Blocker outside this change: corrupt git index

`git status` reports **10 staged deletions for files that exist on disk**, including `alembic/versions/0035`, `0036`, and **`0037` — which migration 0038 declares as its `down_revision`**. Also staged: `app/services/step_sampling.py`, `golden_tests/README.md`, `golden_tests/golden_set.json`.

Cause is an interrupted git operation on 28 Jul: `.git/index.lock`, `.git/HEAD.lock`, and three `.bak` variants are still present. **Committing in this state would delete the migration 0038 depends on.** Clear the stale locks and reset the phantom deletions (`git reset HEAD <paths>`) before committing. Not touched here — repairing a git index is not something to do silently on someone else's repo.

---

## 7. Open Questions

1. **`max_chars = 20,000` is a guess.** It predates this plan and was never tuned. Worth a one-off experiment at 8k / 20k / 40k against fixture `structured.docx`, measuring fact recall vs. cost. Not a blocker — 20k is the safe default.
2. **Cross-source dedup** (transcript + design image describing the same control) is deliberately excluded. It needs a document-level pass and belongs with the parent plan's Phase 2 constrained regrouping.
3. **`.docx` with tracked changes** — should ingestion read the accepted or original text? Current `python-docx` behaviour is undefined here. Needs a decision before an SOW with unresolved redlines is imported.
