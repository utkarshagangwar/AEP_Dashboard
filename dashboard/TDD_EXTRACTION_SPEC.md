# TDD / Skill Extraction Specification

**Status:** implemented (v2)
**Applies to:** any requirements source, any product domain — this spec deliberately contains no AEP-, InterviewGod-, Vidya- or Vikaas-specific rules
**Implementation:** `backend/app/services/tdd_extraction.py`
**Migration:** `0043_tdd_extraction_fields`
**Supersedes:** the single-pass extractor in `design_ingest._SOW_SYSTEM` (retained as `_parse_sow_legacy`, reachable via `TDD_EXTRACTION_V2=0`)

---

## 1. The defect

> "The AI agent is extracting all content that is present in the SOW as TDD, but it should not be. The TDDs must be those which test platform functionality and stability with different types of testing — negative, positive and edge cases."

Two symptoms, one report. They have different causes and need different fixes.

### 1.1 Symptom A — everything becomes a TDD

**Root cause:** the extractor had no definition of what is *not* a requirement.

`_SOW_SYSTEM` opened with *"turning a Statement of Work / requirements document … into QA checkpoints"* and then described the output schema. Nowhere did it say what to skip. A SOW is mostly not requirements — typically 40–70% of it is project overview, scope statements, deliverable lists, commercial terms, timelines, resourcing, assumptions, dependencies, out-of-scope, sign-off procedure, legal clauses, glossary and document history. With no exclusion rule and a strong instruction to be exhaustive, the model converted all of it.

**Amplifier:** `_validate_checkpoint` was written never to drop anything —

> *"An under-specified requirement is FLAGGED (review_status), never dropped. Dropping was the previous behaviour and it was the worst available option…"*

That policy is correct **for a vague requirement**. It was being applied to text that was not a requirement at all. The result: no stage in the pipeline could remove a non-requirement, so every one survived to the Skills table.

**Second amplifier:** extraction runs per chunk (`SowPart`). A chunk containing only the payment-terms table still received "extract QA checkpoints from this document", with no way to answer "there are none here". Models under that framing invent output rather than return empty.

### 1.2 Symptom B — everything that *is* a TDD is a happy path

**Root cause:** the prompt mandated exactly one checkpoint per flow.

> `"ONE CHECKPOINT PER FEATURE… a feature with several distinct user flows (create / edit / delete…) produces a SEPARATE checkpoint per flow"`

Separate checkpoints per *flow*, never per *test type*. The only place a failure case could go was `'notes': an array of caveats, edge cases…` — and notes are rendered into the skill markdown but are **not independently executable**. Nothing in the pipeline ever became a runnable negative test. Coverage was ~100% positive by construction.

**Third cause, underneath both:** the pipeline never classified *what kind of behaviour* a requirement described. Whether something is an input form, an RBAC rule, a scoring formula or an AI generation surface determines *which* negative and edge cases are mandatory. With no classification step, nothing could demand the right probes.

### 1.3 Why a bigger prompt was not the fix

The obvious patch — "add 'also write negative and edge cases' to the prompt" — fails for a structural reason. One LLM call was already doing four jobs: decide what is a requirement, write executable steps, self-assess readiness, and format JSON. Adding a fifth makes the model trade quality between them silently, and there is no way to tell afterwards which job it dropped. The fix is to split the jobs into stages where each has a *deterministic check* on its output.

---

## 2. Principles

These are non-negotiable and every design decision below traces to one of them.

| # | Principle | Consequence |
|---|---|---|
| P1 | **Extract behaviours, not text.** A behaviour is `actor + trigger + observable system response + rule`. If all four cannot be named, it is not a behaviour. | Prose that describes the *project* rather than the *product* yields nothing. |
| P2 | **A test type is a property of the behaviour class, not of the document.** | Categorise first, then the category *declares* which variants are mandatory. |
| P3 | **Nothing is silently lost.** | Every exclusion is recorded with its reason and stored; every under-specified requirement is flagged, not dropped. |
| P4 | **Fail open.** | Every classification stage degrades to "treat it as testable" on error. Losing tokens is recoverable; losing a requirement is not. |
| P5 | **Trust evidence, not the model's claim.** | Coverage requirements are enforced in Python against `CATEGORIES`, not assumed because the prompt asked. |
| P6 | **Derived ≠ stated.** | A negative case reasoned from QA practice is labelled `grounding="derived"` so a failure can be triaged as a possible *spec gap* rather than a product defect. |
| P7 | **The test type must travel with the goal text.** | A negative skill *passes* when the system refuses. An agent reading only "Objective: submit the form" reports the correct refusal as a defect. |

---

## 3. The model

```
Document
  └── Zone            Stage 0 — testable? (else recorded + excluded)
        └── Behaviour Stage 1 — actor + trigger + response + rule
              ├── Category   Stage 2 — one of CATEGORIES; declares required variants
              └── Variants   Stage 3 — positive / negative / edge, each an independent TDD
                    └── TDD  Stages 4-5 — validated, deduped, scored
```

One behaviour produces **N test definitions**, not one. This is the single most important change: the unit of extraction moved from *paragraph* to *behaviour*, and the unit of output moved from *behaviour* to *variant*.

---

## 4. Stage 0 — the testability gate

### 4.1 The rubric

A segment is **testable** when it describes something a tester could observe a running system doing:

- a user action and its result
- a rule the system enforces
- a value it computes or stores
- a message, state or output it produces
- a permission it checks

A segment is **not testable** when it describes the **project** rather than the **product**.

Three judgement calls, stated explicitly because they are where classifiers go wrong:

1. **Aspiration is not behaviour.** *"The platform will streamline hiring"* is background. *"A recruiter can shortlist a candidate from the list view"* is behaviour.
2. **A non-functional target is testable iff it names an observable threshold.** *"Results load within 3 seconds"* — testable. *"The system will be fast and reliable"* — background.
3. **Mixed segments are testable.** If any part of a segment describes behaviour, keep the whole segment. Extraction downstream can ignore surrounding narrative; it cannot recover a segment the gate removed.

**Tie-break rule: when undecided, mark testable.** A wrongly included segment costs a few tokens and produces at most a weak checkpoint a human deletes. A wrongly excluded segment removes a requirement from testing permanently and invisibly. The costs are not symmetric and the gate must not pretend they are.

### 4.2 Exclusion taxonomy

Platform-agnostic by construction — these are section kinds every commercial SOW/PRD/BRD carries regardless of product domain.

| `zone_kind` | Typical headings | Why not testable |
|---|---|---|
| `commercial` | Pricing, Cost, Payment Terms, Rate Card | Describes what is paid, not how the product behaves |
| `schedule` | Timeline, Milestones, Phases, Effort Estimate | A project plan |
| `resourcing` | Team Structure, Roles & Responsibilities, RACI | Who does the work |
| `assumptions` | Assumptions, Dependencies, Prerequisites | Delivery preconditions |
| `out_of_scope` | Out of Scope, Exclusions, Non-Goals | **Must not be tested** |
| `acceptance_process` | Sign-off, Approval Process, UAT Process | Governance, not behaviour |
| `change_control` | Change Control, Variation Procedure | Contractual process |
| `legal` | Confidentiality, IP, Liability, Warranty, T&Cs | Contract |
| `glossary` | Glossary, Definitions, Acronyms | Vocabulary |
| `doc_control` | Version History, Table of Contents, Approvals | Document metadata |
| `background` | Executive Summary, Introduction, Business Objectives, Problem Statement | States intent, not verifiable behaviour |
| `support_terms` | Support Model, Maintenance Period, Hypercare | Post-delivery contract |
| `methodology` | Methodology, Ways of Working, Governance, Ceremonies | How the team works |
| `tooling` | Tech Stack, Infrastructure Overview, Hosting | What it is built with, not what it must do |

### 4.3 How the gate runs

**Step 1 — segment.** `split_segments()` breaks the part on markdown headings *and* on the numbered/uppercase headings that PDF and DOCX conversions produce (`4.3 Scope of Work`, `SECTION 5 — ASSUMPTIONS`). Segments shorter than 60 chars merge forward — a bare heading carries too little signal to classify. Text before the first heading becomes a `heading=None` segment rather than being discarded.

*Invariant: zoning may only **select** text, never rewrite it. The concatenation of all segment bodies is the input.*

**Step 2 — deterministic pass.** `deterministic_zone_verdict()` matches the heading against the taxonomy regexes. Free, instant, and it handles the majority of exclusions in a typical SOW, so a contract-heavy document costs almost nothing to gate.

**The behaviour-marker veto.** A heading match is *cancelled* if the body contains behaviour markers (`shall`, `must`, `the system can`, `clicking`, `error message`, `validation`, `dropdown`, `redirect`, …). An "Assumptions" section that ends with *"however, the system must still reject duplicate emails"* is passed to the LLM zoner instead of being dropped. This is P4 in code.

**Step 3 — LLM pass** over segments the rules did not settle. Sends heading + a 1,200-char body preview only: zoning is a judgement about what *kind* of content this is, which the opening of a section settles. Sending full bodies would cost as much as extraction itself.

**Step 4 — safety valve.** If the combined verdict excludes more than **85% of the part's characters**, the entire zoning result is discarded and the whole part goes to extraction. A verdict that aggressive is far likelier to be a classifier malfunction than a document with no requirements. Logged at `ERROR`.

**Step 5 — record.** Every exclusion is written to `sow_parts.excluded_zones`:

```json
{"heading": "7. Commercial Terms", "zone_kind": "commercial",
 "reason": "commercial terms — describes what is paid, not how the product behaves",
 "char_count": 1340, "classifier": "deterministic"}
```

This column is why the gate is safe to have at all. The alternative to recording exclusions is a filter nobody can audit — and *"the extractor quietly decided your requirements section was a glossary"* is exactly the failure that would otherwise be impossible to notice.

**Cost note.** The gate is roughly token-neutral: it adds a short classification call but removes contract/timeline/glossary text from the much larger extraction call, and a part that is entirely non-testable skips extraction altogether.

---

## 5. Stage 1 — behaviour extraction

The extraction prompt (`build_extraction_system()`) enforces five rules. Rules 1 and 5 exist to stop symptom A; rules 2 and 3 to stop symptom B.

**Rule 1 — extract behaviours, not text.** Actor + observable response + rule, or it is not output. Explicit instruction that returning zero behaviours for a passage is the *correct* answer, and an explicit ban on the checkpoint shapes that gave the defect away:

> Never emit a checkpoint whose objective is to *"verify the document states…"*, *"confirm the scope includes…"*, *"check that the platform supports…"* in the abstract, or to review a deliverable. Those are document-reading tasks, not product tests.

**Rule 2 — every behaviour gets variants** (§7).

**Rule 3 — grounding** (§6.3).

**Rule 4 — runnable, atomic, independent.** Unchanged from v1 and still correct: `role` / `objective` / `context` / `instructions` / `notes`, one action per instruction, ending with the verification step. One addition, because it is where negative tests are most often written wrong:

> For a negative checkpoint, PASS is the refusal. Write *"the form is rejected and no job is created"*, never *"the form fails"*.

**Rule 5 — honesty over completeness-theatre.** `review_status` semantics carried over unchanged: `needs_review` (stated but under-specified), `needs_design_flow` (implies a flow never described), `ready` (every step grounded). Never fabricate steps to make a requirement look complete.


### 5.1 The UI naming reference (Stage 1 input)

Extraction reads a document. The product it describes has its own vocabulary, and the two rarely match: the SOW says *"click Submit Application"*, the button reads *"Apply Now"*. The test then fails for a reason that is **neither a product defect nor a spec gap** — the most demoralising red result there is, because it looks like a bug and is not.

`ui_inventory.build_inventory()` fixes the vocabulary, not the pipeline. One vision call per **project** reads the uploaded evidence — screenshots (`figma_png` artifacts), plus control and field names already recovered from digested walkthrough videos, which is text the video digest already paid for — and records what each screen, button, field and nav item is *called*. The result is stored on `project_ui_inventory` and appended to the extraction and repair prompts.

**Why not live navigation.** Driving the real product would ground the same labels at per-test cost, and would require working credentials and a deployed environment at extraction time. Today a SOW for a product that does not exist yet still extracts; that property is worth keeping. This is one call per project, reused by every SOW imported for it.

**The hard rule: vocabulary, not requirements.** A button visible in a screenshot is not evidence that anyone asked for it to be tested. If the inventory could introduce behaviours it would reintroduce symptom A (§1.1) from the opposite direction, so the rule is stated in the vision prompt, restated in `format_for_prompt()` at the point of use, and pinned by a test. The prompt also tells the model what to do when a control is *absent* from the reference — use the document's wording, and do not conclude the control is missing — because the reference is partial by nature.

**Transcribe, never paraphrase.** A wrong label is worse than a missing one: a missing label falls back to the document's wording, which a reader can see and judge; a wrong one looks authoritative and sends a test to a control that does not exist. Anything unreadable, cut off or ambiguous is dropped.

**Staleness is keyed on evidence, not time.** `source_artifact_ids` stores every evidence artifact that *existed* at build time — not the subset the build managed to use. Keying on "used" would leave the stored set permanently unequal to the current set whenever a screenshot was skipped as oversized, capped by `_MAX_IMAGES`, or belonged to a video still digesting, and every part of every SOW would rebuild and pay for another vision call. When the evidence set changes, the inventory rebuilds; that is the whole answer to *"we uploaded screenshots after importing the first SOW"*.

**Fail open, always.** No project, no evidence, a failed vision call, an unreadable file: every path returns `None`, and every caller responds identically — extract from document text alone, exactly as before this existed. A vision call cannot fail a SOW ingest.

---

## 6. Stage 2 — behaviour categories

### 6.1 The category *contract*

A category is not a reporting label. It is a **contract** that declares:

- `when` — the condition under which a behaviour belongs to it
- `requires` — the test types that **must** exist (enforced in code, §8)
- `negative` / `edge` — the specific probes a tester must attempt

This is what makes the extractor platform-agnostic. The probes are properties of the *behaviour class* — "something that accepts user input", "something that computes a number", "something that feeds untrusted text to a model" — never of a particular product. Adding a new behaviour class is one entry in `CATEGORIES`; no prompt surgery and no per-platform branching anywhere else.

`render_category_reference()` builds the prompt's contract table **from the same dict** the validator enforces, so the prompt and the enforcement cannot drift apart.

### 6.2 The taxonomy

**Generic product behaviour**

| Code | Applies when | Negative probes | Edge probes |
|---|---|---|---|
| `input_validation` | user-supplied data is accepted | required empty; wrong format; forbidden value → blocked with a visible message and **no partial record** | min/max ±1; whitespace-only; unicode; paste vs type |
| `authentication` | login, OTP, password, token, session | wrong credential; expired/replayed token; locked account; protected route while signed out | expiry exactly at boundary; concurrent sessions; refresh mid-request; back-button after logout |
| `authorization` | capability limited by role/tier/owner/tenant | role without permission blocked **in UI *and* by calling the endpoint directly**; other tenant's record unreachable by id | last remaining privileged user; permission revoked mid-session; multi-scope user |
| `crud` | record created / listed / edited / removed | duplicate of a unique value; edit or delete of a vanished record; delete of a referenced record | empty list; first/last page; two-user concurrent edit; delete then re-create same id |
| `state_transition` | status/stage moves, or a step gates a later step | forbidden transition rejected; gated step stays locked before its precondition | terminal state; same transition twice; transition during an in-flight job |
| `search_filter_sort` | results queried, narrowed, ordered | no matches → defined empty state, not an error or stale list; malformed query handled | special chars/wildcards; combined filters; reset restores full set; sort stability |
| `file_io` | a file crosses the boundary either way | unsupported type; corrupt/truncated; oversize; cancelled mid-transfer | exactly at the limit; 0-byte; non-ASCII filename; replacement leaves no orphan; export matches screen |
| `calculation` | the system computes a number | missing/zero operand **not** silently treated as pass/default; formula asserted against a known input set | boundaries either side of every cut-off; rounding; divide-by-zero; partial input scored only on what exists |
| `integration` | external service, webhook, sync, callback | dependency errors or is unreachable → degrades visibly, does not hang or silently no-op | timeout; duplicate callback delivery; partial sync; credentials expiring mid-session |
| `notification` | something is sent to a person outside the UI | delivery failure surfaced, not swallowed; **not** sent when the triggering action failed | no side effects beyond the message; duplicate trigger → one message |
| `payment_billing` | money, subscription, credits or quota change | decline leaves no entitlement and no partial record | retry must not double-charge; balance hitting zero mid-action; refund/rollback path |
| `resilience` | interruption, failure, retry, recovery, offline, timeout | unrecoverable failure → defined terminal state, no partial write, no partial result shown | recoverable interruption vs unrecoverable failure that look identical must not be confused; repeated manual retries → no duplicate side effect |
| `localization` | more than one language/locale/currency/format | unsupported locale not offered and cannot be forced; no silent fallback | propagation to **every** surface (UI, generated content, captions, exports, notifications); mid-flow switch; RTL and long strings |
| `performance_latency` | a response time or duration is specified/displayed | long operation shows progress, never times out silently | first run with no history → empty state, not an error; displayed metric matches real elapsed time |
| `data_integrity` | data must survive reload/session or stay consistent across views | abandoned or failed operation leaves no half-written record; unsaved-changes warning instead of silent discard | same entity consistent in two places; refresh mid-edit; value round-trips unchanged |
| `visual_layout` | how something looks | *(positive only)* | *(positive only)* |

**AI-specific behaviour.** These exist because an AI surface fails in ways a deterministic one cannot: it degrades silently, it can be talked out of its instructions, and its output shape is a probability rather than a contract. Derived from the AI Vibe Testing mind map (Part A) and generalised away from any particular product.

| Code | Applies when | Negative probes | Edge probes |
|---|---|---|---|
| `ai_prompt_config` | a prompt/template/rulebook driving AI output is editable or selectable | empty/invalid prompt save blocked and fires no save call; mutually exclusive options cannot both be selected | reset-to-default on a scope with no override; unsaved-edit navigation warning; long prompt persists byte-for-byte — **verify the stored text, not the re-rendered UI** |
| `ai_generation` | a model produces content/score/report/decision reaching a user | malformed, empty or truncated response → defined error state; never a blank screen, crash, or plausible placeholder | unexpected extra / missing optional fields don't break rendering; in-progress state distinguishable from final; repeat-run variance within the stated bound |
| `ai_untrusted_input` | any user text, file, transcript or third-party content enters a prompt | embedded instructions ("ignore previous instructions", "award full marks") do not change behaviour or leak the system prompt | the same injection through **every other channel** that reaches the prompt — uploaded file, transcript, imported record, filename |
| `ai_scoring` | model output becomes a score/grade/band/pass-fail | unattempted or empty input must not score as a pass; a score must not appear without the evidence the spec pairs with it | exact band boundaries both sides; partial completion scored only on what was submitted; components the spec keeps separate not merged into the overall |
| `ai_context` | earlier context is reused by a later AI interaction | context source absent → generic fallback, not references to data that doesn't exist | two sources disagree → deterministic documented precedence; **no context leak across users/tenants/workspaces** |
| `ai_explainability` | the spec requires the AI to show why | result rendered with reasoning missing is a defect, not a cosmetic gap | reasoning traces to the actual input, not boilerplate repeated across subjects; a composite result's reasoning reflects the composition |
| `media_capture` | the flow depends on live audio/video/device permission | permission denied blocks exactly as specified; mid-flow disconnect produces the specified outcome, not an ambiguous hang | degraded input degrades gracefully rather than failing falsely; brief drop-and-recover not treated as hard failure unless specified; caption/transcript state consistent with audio state |

**Tie-break:** a behaviour touching an AI surface takes the `ai_*` category over the generic one. The AI risks are the ones nothing else will catch.

### 6.3 Grounding

| Value | Meaning | Triage implication |
|---|---|---|
| `stated` | the source document explicitly specifies this expectation | a failure is a product defect |
| `derived` | inferred from standard QA practice because the document is silent | a failure **may be a spec gap** — confirm with the spec owner |

Most negative and edge checkpoints are `derived`, and that is correct and wanted: documents rarely enumerate their own failure modes.

**Closing the loop.** `GET /api/ai-testing/spec-gaps` lists derived skills that have **never once passed** across at least `min_runs` decided runs. Consistency is the point: a derived test that sometimes passes is flaky, environment- or data-dependent — product and infra concerns. One that has never passed is a systematic disagreement between the inferred expectation and the product, which is what a spec gap looks like. Undecided runs (`needs_review`, `inconclusive`, `cancelled`, `pending`, `running`) are excluded from both the numerator and the denominator: treating "we don't know" as "it failed" would manufacture spec gaps out of infrastructure problems. The rule lives in `is_spec_gap_candidate()`, separated from the endpoint so it is testable without a database. It produces **candidates, not a verdict** — a never-passing derived test can still be a real long-standing defect; the point is that it can no longer only be read that way. Rendered in the AI Testing → Coverage tab.

**The constraint that keeps derived tests honest:** a derived checkpoint must assert a *generic safe outcome* — *"the submission is rejected with a visible error and no record is created"* — and must **never invent a specific unstated detail**: no exact error string, no exact limit, no exact status name the document does not give. Where the document is silent on a specific value, that goes in `notes`, not into `instructions`.

---

## 7. Stage 3 — variant generation

| Type | Definition | What PASS means |
|---|---|---|
| `positive` | the intended path | the stated outcome is observable |
| `negative` | the system is given something it must **refuse**, or a dependency fails | it refuses/degrades **correctly and safely**: visible specific message, no partial write, no elevated access, no silent success |
| `edge` | a boundary, empty/maximum value, concurrent or interrupted action, unusual-but-legal input | behaviour is **defined and consistent** — not necessarily that the action succeeds |

Each variant is an **independently runnable TDD** with its own `role`/`objective`/`context`/`instructions`. Not a note, not a bullet under the happy path.

The rendered skill markdown leads with a banner for non-positive types (P7) — the agent executing the goal has no access to the database column:

```
# ⛔ Negative test
This test PASSES when the system correctly REFUSES or safely rejects the action
below. A visible, specific error with no partial data saved and no access granted
is a PASS. The action succeeding is a FAIL.

Expectation source: standard QA practice, NOT stated in the source document. If
the product's actual intended behaviour differs, confirm with the spec owner
before raising a defect.
```

### 7.1 Variant expansion for observed sources (video walkthroughs)

The video digester works under a strict *"describe only what the recording shows"* rule. That rule is correct and is **not relaxed** — a video is evidence, and inventing behaviour from it turns a walkthrough into fiction. But it also means a video can only produce happy paths, because a demo only demonstrates things working.

`classify_and_expand()` therefore runs as a separate, explicitly-labelled pass: it categorises what was observed (`grounding="stated"` — observed on screen is the strongest grounding there is) and derives the negative/edge cases the category requires, all `grounding="derived"`. Observed checkpoints are never rewritten, only tagged. Derived cases reuse the exact control and field labels read off the screen, since those are the only labels known to be real. The pass never raises: a video that digested successfully is not failed by enrichment.

It then runs the **same backstop the SOW path gets** — `apply_variant_backstop()` (Stages 4 + 4c over a flat list, grouping on `behaviour_key`) followed by `repair_coverage_gaps()`. The expansion prompt *asks* for the variants each category requires; the backstop checks in code that they arrived (P5), re-requests the ones that did not, and bounds a behaviour that produced too many. Before this, Stages 4/4b/4c were silently SOW-only, so a walkthrough-derived behaviour was held to a visibly lower standard than a document-derived one — and two sources feeding one Skills table with two different levels of rigour is exactly the difference nobody remembers when reading the results. Checkpoints with no `behaviour_key` (visual ones, legacy rows) pass through untouched: they have no category contract to enforce.

---

## 8. Stage 4 — the deterministic backstop

`check_variant_coverage()` verifies the category's `requires` set **in Python**. The model is asked for the required variants; this checks that it produced them (P5).

A behaviour missing a required variant is **neither dropped nor silently accepted**. Its positive checkpoint carries:

```json
"coverage_gap": ["negative", "edge"]
```

which the scorecard counts and the UI can surface. Same principle the existing pipeline already applies to `review_status`: flag on the evidence, do not trust the model's own claim about its work.

### 8.1 Stage 4c — the variant cap

A rich behaviour legitimately produces many variants — an `input_validation` rule over several fields has a distinct negative case per field, and all of them are real tests. Nothing bounded that. The only thing between a verbose behaviour and an unbounded skill list was the extraction call's `max_tokens`, which truncates the model's JSON at whatever character it reaches: the tests you lose are chosen by accident, the loss is invisible, and it lands mid-array so it can corrupt a well-formed checkpoint on the way out.

`cap_variants()` replaces that accident with a decision. Ceiling is per **behaviour** (`_MAX_VARIANTS_PER_BEHAVIOUR = 8`), not per part or document — a document with fifty modest behaviours is fine and must not be trimmed; one behaviour with twenty variants is where the runaway actually happens.

Selection order:

1. **One checkpoint of every test type present, before anything else.** Dropping the only edge case to keep an eighth negative would gut `negative_edge_ratio` and remove a category's required coverage — the opposite of this pipeline's purpose.
2. Fill remaining slots by `priority` (`smoke` > `sanity` > `regression`), ties broken on document order so the result is stable across re-analysis.
3. Restore **document order** for the survivors: selection is by priority, presentation is not.

Runs **after** Stage 4 and before Stage 4b. Capping before the coverage check would drop a required variant, Stage 4 would flag it as a gap, Stage 4b would re-request it, and the cap would drop it again — a loop that spends tokens forever and converges on nothing.

**No silent caps.** The dropped tests are named in a `WARNING`, the surviving anchor carries `capped_variants: N`, and the scorecard sums it — so "this part produced 40 checkpoints" is distinguishable from "produced 55, kept 40". An unrecognised `priority` sorts last rather than raising.

### 8.2 Stage 4b — gap repair

Detecting a hole and shipping it anyway is still a hole. `repair_coverage_gaps()` re-asks for **only** the missing variants before dedupe runs.

This is a much narrower question than the original extraction — the behaviour, its category and its happy path are already known and supplied, so the model writes one specific test instead of deciding what is a requirement, how to categorise it and how to phrase five things at once. That is §1.3's job-splitting argument applied one level down.

Three properties, in order of importance:

1. **Coverage is recomputed from the result, never from the reply.** A variant the model claimed but that failed validation, or quietly skipped, leaves `coverage_gap` standing — reduced to whatever is still missing. Only a behaviour whose gap is genuinely closed loses the flag. This is P5 again: the repair stage is not permitted to grade its own work.
2. **It never fails a parse.** Every failure path returns the input unchanged. Losing a part's real checkpoints to a failed enrichment call is far worse than shipping the gap the flag already documents.
3. **Everything it produces is `grounding="derived"`**, unconditionally and regardless of what the model claims — a repaired variant is reasoned from QA practice, not read out of the document (P6).

Two narrowing rules stop the stage widening the output: a `behaviour_key` that was not asked about is dropped, and a `test_type` that was not missing is dropped. Silently widening output is precisely how symptom A behaved.

Capped at `_MAX_REPAIR_BEHAVIOURS` (12) behaviours per part. A part with more gaps than that has a systemic problem another LLM call will not fix (§10: gaps above ~10% of behaviours means investigate the prompt or the provider). Behaviours beyond the cap keep their flag and are **named in a `WARNING`** — a silent cap reads as "everything was repaired".

---

## 9. Stage 5 — dedupe

`dedupe()` collapses exact repeats of `(behaviour_key, test_type, normalised objective)`.

Deliberately narrow. Two checkpoints with the same behaviour and test type but **different objectives** are two different cases — an input form has many distinct negative cases — and both are kept. Only a literal repeat collapses (the same behaviour restated in an overview section and again in a detail section). First occurrence wins, so document order is stable.

This is a change from the previous cross-part merge, which was documented as *"no cross-chunk dedup — simple, predictable concatenation"* and produced duplicate skills whenever a feature appeared in both a summary and a detail section.

### 9.1 Stage 6 — cross-part reconciliation

`dedupe()` runs *inside* `extract()`, so it only ever sees one part. A SOW almost always describes a feature twice — once in a summary or scope section, once in detail — and those land in different parts with different `behaviour_key`s, because the model named them differently. No per-part stage can see that.

`reconcile_across_parts()` runs at the **document** level, in `_merge_checkpoints`, with the two halves deliberately built on different mechanisms:

| Step | Decided by | Why |
|---|---|---|
| **Merging** — is this the same test? | Python: `difflib` similarity on the objective, within a test type, across parts only | It must not be wrong, so it doesn't get to be creative. Deterministic and free, so it runs on every part completion — which is what stops a duplicate Skill being *created*, rather than cleaning one up afterwards. |
| **Naming** — is this the same behaviour? | LLM, mapping near-duplicate `behaviour_key`s onto a canonical one | Naming is a judgement call. Restricted to keys actually sent; fails open to identity. Runs **once**, when the last part lands (`finalize=True`), so a 12-part document pays for one call rather than twelve. |

Merging deliberately does **not** require matching `behaviour_key`s — two parts naming the same behaviour differently is exactly the case that must merge. Naming affects only how variants *group*; it never decides which survive.

**The asymmetry that sets the threshold.** Failing to merge two duplicates costs a cosmetic duplicate skill someone deletes. Wrongly merging two *different* tests silently deletes a test — and nobody finds out, because the thing that would have reported it is the test that no longer exists. Those costs are not comparable. Hence `_RECONCILE_OBJECTIVE_THRESHOLD = 0.90` and three hard guards:

- **never across test types** — a positive absorbing a negative would delete exactly the coverage this pipeline exists to produce;
- **never within one part** — Stage 5 already deduped there, so what remains is deliberately distinct (a rich `input_validation` behaviour legitimately has several similarly-worded negative cases);
- **first occurrence wins** — document order stays stable, and re-analysing a later part cannot reshuffle the list.

**Nothing is lost, and the merge is visible.** The survivor carries `merged_from_parts: [4]`, surfaced on the API and rendered in the SOW tab. `SowPart.checkpoints` is left **untouched** — it stays the record of what that section actually produced, and reconciliation is a document-level view over it, the same select-don't-rewrite rule Stage 0 follows for text. The absorbed indices are returned to the worker so it skips creating the duplicate Skill.

---

## 10. Stage 6 — the coverage scorecard

`scorecard()` output, stored per part in `sow_parts.coverage_json`:

```json
{
  "total_checkpoints": 42,
  "by_test_type": {"positive": 18, "negative": 15, "edge": 9},
  "by_category": {"input_validation": 12, "authorization": 8, "ai_scoring": 6, "...": 0},
  "by_grounding": {"stated": 20, "derived": 22},
  "ai_category_checkpoints": 11,
  "negative_edge_ratio": 0.571,
  "needs_review": 3,
  "coverage_gaps": [{"behaviour_key": "delete-user", "category": "authorization", "missing": ["negative"]}],
  "excluded_zone_count": 6,
  "excluded_zone_kinds": ["commercial", "doc_control", "glossary", "schedule"]
}
```

### Acceptance gates

Run these against the scorecard for a whole document. They are the regression test for extraction **quality**, as opposed to extraction *failure* — which is the class of problem that is otherwise invisible until someone reads all the generated skills.

| Metric | Gate | Rationale |
|---|---|---|
| `negative_edge_ratio` | **≥ 0.40** | On the old pipeline this was ~0.0 by construction. Below 0.40 the extractor has drifted back to happy-path-only output. |
| `coverage_gaps` | **≤ 10%** of behaviours | Above that, the model is systematically ignoring the category contract — investigate the prompt or the provider, not the document. |
| `excluded_zone_count` | **> 0** on any real commercial SOW | Zero exclusions on a document that certainly contains a pricing or timeline section means the gate is not firing. |
| excluded char ratio | **< 0.85** | Enforced automatically by the safety valve (§4.3 step 4); an `ERROR` in the log means it triggered. |
| `by_grounding.stated` | **> 0** | All-derived output means extraction is inventing rather than reading. |
| `ai_category_checkpoints` | **> 0** on an AI product spec | Zero means AI-specific risks (injection, silent degradation, band boundaries) were never probed. |

---

## 11. Data model

### `ai_skills` (migration 0043)

| Column | Type | Meaning |
|---|---|---|
| `test_type` | `varchar(20)`, indexed | `positive` \| `negative` \| `edge` |
| `category` | `varchar(50)`, indexed | a `CATEGORIES` code |
| `grounding` | `varchar(20)` | `stated` \| `derived` |
| `behaviour_key` | `varchar(120)`, indexed | groups a behaviour's variants |
| `priority` | `varchar(20)` | `smoke` \| `sanity` \| `regression` → Robot Framework `[Tags]` |

### `sow_parts` (migration 0043)

| Column | Type | Meaning |
|---|---|---|
| `excluded_zones` | `jsonb` | the gate's audit trail (§4.3 step 5) |
| `coverage_json` | `jsonb` | the scorecard (§10) |

**All columns are nullable and unbackfilled, deliberately.** There is no honest way to infer after the fact whether an existing skill was a positive or a negative case — the information was never captured. Writing a guess ("everything existing is positive") would be indistinguishable from a real classification later. Readers treat `NULL` as *unclassified, assume the conservative reading*. **Re-analysing an artifact is the migration path**: it reproduces the skills through the v2 extractor with real values.

### Operational consequence

`test_type` is the field a reviewer cannot work without. A red result on a **negative** skill means the product *accepted* something it should have rejected — the opposite reading from a red positive skill. Surface it wherever a run result is shown.

---

## 12. Configuration

All flags are **opt-out** (unset = enabled), matching the existing `SOW_AUTO_ANALYZE_PARTS` convention.

| Variable | Default | Effect when disabled (`0`) |
|---|---|---|
| `TDD_EXTRACTION_V2` | on | Falls back to the legacy single-pass extractor: no testability gate, no variants, no categories. Escape hatch for a provider that cannot hold the larger v2 prompt. **Not a recommended configuration** — logs a warning on every part. |
| `TDD_ZONING` | on | Skips Stage 0; extracts from the whole part. Use to isolate whether a missing requirement was lost to the gate or to extraction. |
| `TDD_DERIVED_AS_SKILLS` | on | Derived negative/edge checkpoints stay in the checkpoint list but do not become Skills. Use if the team wants to review inferred expectations before they become runnable tests. |
| `TDD_UI_INVENTORY` | on | No UI naming reference is built or injected (§5.1). Extraction sees document text only, so generated instructions name controls the way the document does. Use to isolate whether a wrong label came from the document or from the inventory. |
| `TDD_VARIANT_CAP` | on | Removes the per-behaviour variant ceiling (§8.1); a behaviour keeps every variant the model produced, bounded only by the output-token limit as before. |
| `TDD_RECONCILE` | on | Skips Stage 6 (§9.1): no cross-part merging, so the same feature described in a summary and again in a detail section stays as two sets of checkpoints and two Skills — the pre-reconciliation behaviour. Use to isolate whether a missing checkpoint was merged away or never extracted. |
| `TDD_GAP_REPAIR` | on | Skips Stage 4b (§8.1). A behaviour missing a required variant keeps its `coverage_gap` flag and the variant is never written — the pre-repair behaviour. Use to isolate whether a bad checkpoint came from extraction or from repair, or to cut the extra call on a token-constrained provider. |

---

## 13. Code map

| Concern | Location |
|---|---|
| Engine (all six stages) | `backend/app/services/tdd_extraction.py` |
| Category contract | `tdd_extraction.CATEGORIES` |
| Exclusion taxonomy | `tdd_extraction.NON_TESTABLE_ZONES` |
| Extraction prompt | `tdd_extraction.build_extraction_system()` |
| UI naming reference | `services/ui_inventory.py` |
| Naming-reference prompt | `ui_inventory._INVENTORY_SYSTEM` |
| Prompt framing | `ui_inventory.format_for_prompt()` |
| Inventory storage | `ProjectUiInventory` (migration `0045`) |
| Inventory read API | `GET /api/v1/visual-audits/projects/{id}/ui-inventory` |
| Variant cap (Stage 4c) | `tdd_extraction.cap_variants()` |
| Flat-list backstop (video path) | `tdd_extraction.apply_variant_backstop()` |
| Gap repair (Stage 4b) | `tdd_extraction.repair_coverage_gaps()` |
| Gap-repair prompt | `tdd_extraction._REPAIR_SYSTEM_HEAD` |
| Reconciliation (Stage 6) | `tdd_extraction.reconcile_across_parts()` |
| Behaviour-name consolidation | `tdd_extraction.canonicalize_behaviour_keys()` |
| Document-level merge | `workers/tasks/sow_ingest._merge_checkpoints` |
| Quality gate (log warning) | `tdd_extraction.ratio_gate_warning()` |
| Spec-gap rule | `api/v1/ai_runs.is_spec_gap_candidate()` |
| Spec-gap endpoint | `GET /api/ai-testing/spec-gaps` |
| Zoning prompt | `tdd_extraction._ZONING_SYSTEM` |
| Video expansion prompt | `tdd_extraction._EXPANSION_SYSTEM_HEAD` |
| Entry point (detailed) | `design_ingest.parse_sow_detailed()` |
| Entry point (narrow) | `design_ingest.parse_sow()` |
| Legacy extractor | `design_ingest._parse_sow_legacy()` |
| Shared validation | `design_ingest.validate_checkpoint()` |
| Skill markdown rendering | `design_ingest.render_skill_markdown()` |
| Persistence | `workers/tasks/sow_ingest._analyze_part`, `_save_functional_skills` |
| Skill upsert | `services/skill_store.upsert_prompt_skill()` |
| Tests | `backend/tests/test_tdd_extraction.py` |

---

## 14. Extending this to a new platform

The intended answer to *"how do I make this work for product X"* is **you don't** — nothing in the pipeline is product-aware, and adding a product-specific rule is the wrong fix.

What you extend instead is the **behaviour taxonomy**:

1. Identify the behaviour class, not the product feature. *"Vidya's certificate gating"* is not a category; `state_transition` is. *"IG's hybrid score"* is not a category; `calculation` + `ai_scoring` are.
2. If the class genuinely does not exist yet, add one entry to `CATEGORIES` with `when`, `requires`, `negative`, `edge`.
3. Add a case to `test_every_category_declares_a_usable_contract`.

The prompt updates itself (`render_category_reference()` renders from the dict) and `check_variant_coverage()` starts enforcing it in the same commit. No other file changes.

Same rule for the gate: if a document kind carries a section type not in `NON_TESTABLE_ZONES`, add the heading pattern and the reason there — not a special case anywhere else.

---

## 15. Verification performed

- 252 unit tests pass (`backend/tests/`, excluding two modules that fail only on sandbox-missing `hypothesis` / `litellm`).
- 35 new tests in `test_tdd_extraction.py` cover: segmentation preserving all text, the exclusion taxonomy, the behaviour-marker veto, the exclusion audit-record shape, the safety valve, fail-open on provider error, the category contract's internal consistency, the code-side variant backstop, dedupe narrowness, scorecard arithmetic (including divide-by-zero), and flag defaults.
- Alembic chain verified linear — 43 migrations, single head `0043_tdd_extraction_fields`, no duplicate `down_revision`.
- End-to-end smoke with a stubbed provider: a Commercial Terms section was gated out and recorded; a Job Creation section produced a positive and a `derived` negative checkpoint with the negative banner in its goal text; the missing `edge` variant was flagged as a `coverage_gap`; `negative_edge_ratio` = 0.5.

---

## 16. Known limitations

1. **The gate is heuristic at the margin.** A requirements section titled only *"Notes"* or *"Additional Points"* has no signal in its heading and depends entirely on the LLM zoner. Mitigated by fail-open, the behaviour-marker veto and the recorded audit trail — but read `excluded_zones` on the first run against a new document format.
2. **`derived` tests can produce false defects.** A negative case reasoned from QA practice may assert behaviour the product intentionally implements differently. This is why `grounding` exists and why the banner text says to confirm with the spec owner. `TDD_DERIVED_AS_SKILLS=0` is the conservative posture.
3. ~~**Variant count is not bounded per behaviour.**~~ **Resolved** by Stage 4c (§8.1), which caps by `priority` and logs what it dropped. Residual limit: the ceiling is a fixed constant rather than tuned per category, so a genuinely rich `input_validation` behaviour hits it sooner than a `visual_layout` one ever would.
4. ~~**Cross-part behaviour merging is by exact objective only.**~~ **Resolved** by Stage 6 (§9.1). Residual limit: merging is string similarity on the objective at a deliberately high threshold, so two parts describing one behaviour in genuinely different words still yield two behaviours. That is the intended bias — see the asymmetry note in §9.1.
5. **The scorecard is per part.** A document-level roll-up is a straightforward aggregation over `sow_parts.coverage_json` but is not yet surfaced in the API.
