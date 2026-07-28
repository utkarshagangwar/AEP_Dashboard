# New Vibe Test — Reliability Gaps & Implementation Checklist for Complete Functional Testing

**Update:** the single free-text "goal" entry point (previously proposed as a "Quick Test" option) is dropped entirely. Test creation is split into two separate, purpose-built flows — **UI Test** and **Functional Test** — each with its own form and its own backend pipeline. There is no longer a generic, undifferentiated goal box.

Scope: `POST /ai-testing/runs` (web platform) → `ai_runner.run_ai_test_sync()` (browser-use "Hands" agent) → `generate_narrative_summary()` → `ai_eval.evaluate_run()` (DeepEval GEval second check), plus `visual_judge.judge()` (pixel-diff + vision comparison) for UI tests. All findings below are verified directly against `backend/app/services/ai_runner.py`, `backend/app/workers/tasks/ai_execution.py`, `backend/app/services/ai_eval.py`, and `backend/app/services/visual_judge.py`.

---

## Part 1 — Where It's Lagging Today

### A. Test design — one flow was doing two different jobs
1. **UI and functional testing are conflated into one goal-based flow.** Today, a single free-text goal is expected to cover both "does this look right" and "does this behave correctly" at once. The agent has no signal for which kind of check it's performing, so appearance concerns and behavior concerns get mixed into one unverifiable instruction and neither is checked precisely. Splitting them (Part 2, Phase 1) is the fix.
2. **No structured test case for functional tests.** The only input is one free-text `goal` string. There's no field for preconditions, ordered steps, per-step expected results, or test data — so two runs of "the same test" can take different paths depending on how the agent interprets the wording.
3. **No explicit assertions.** The web agent decides for itself when the goal is "done" (`done=true`); there's no equivalent of Android's `assert` action for the web path, so there's no way to require "field X must show value Y" as a hard checkpoint mid-flow.
4. **No requirement traceability.** A test run isn't linked to a specific SOW requirement or checkpoint ID (that linkage only exists in the separate SOW→Checkpoints pipeline). You can't currently answer "which requirements have a passing test?" from this feature alone.
5. **No negative/edge-case coverage by construction.** The agent only pursues the happy path implied by the goal text. Nothing prompts it to also try invalid input, boundary values, or error states unless you manually write a separate goal for each.
6. **No data-driven / parameterized runs.** You can't run one functional test against five different data sets automatically — each variation needs its own manually written test.

### B. Execution reliability
7. **Chromium only** — confirmed in code (`_find_chromium()`, `pw.chromium.connect_over_cdp`). No Firefox/WebKit coverage, so cross-browser functional bugs are invisible to this feature.
8. **Fixed 1530×820 desktop viewport**, hardcoded (`_VIEWPORT`). No mobile-web or responsive-layout testing — a functional flow that only breaks at a smaller breakpoint won't be caught.
9. **Anthropic/OpenAI key rotation is a documented no-op** in this code path — only Google keys actually rotate on failure, so multi-key redundancy for the other two providers currently does nothing.
10. **Two model IDs look non-standard and unverified**: `gemini-3.5-flash` (used as a fallback preference) and `google/gemma-4-26b-a4b-it:free` (OpenRouter default). If either is wrong/stale, the system silently falls through to the next model every time, wasting latency with no alert.
11. **No prompt-injection defense** — the agent reasons directly over live, untrusted page content with no separation from the trusted goal text. A test running against a compromised or malicious page could be steered off-script.
12. **`allow_unrestricted_domains=True`** is applied unconditionally for ad-hoc login runs — credentials can be typed on any domain the page redirects to, by design, with no allowlist at all.

### C. Observability — it goes blind on long runs
13. **Both the narrative summary and the GEval score are built from only the FIRST 60 recorded steps**, each truncated to 300 characters — confirmed in `ai_runner.generate_narrative_summary()` and `ai_eval.evaluate_run()`. A run can have up to 100,000 steps; if something goes wrong near the end (the most common place a multi-page functional flow actually fails), neither the human-readable summary nor the independent quality score will ever see it.
14. Screenshot capture failures are swallowed silently in several places with no log — visual evidence can go missing from a run with no trace.

### D. The "second AI check" (GEval) — exists, but weaker than it looks (Functional Test only)
15. **GEval never gates the run's pass/fail status.** Verified directly in `_persist_result()`: `run.status` is set from the agent's own self-reported engine result; `eval_score`/`eval_reason` are stored purely as *extra, informational* fields on the same row. A run can show status **"passed"** in the UI even if GEval scored it well below its own 0.5 threshold — nothing currently stops that from happening or flags it for review.
16. **GEval is explicitly skipped for Android** (`platform == "web"` only, by comment-documented design) and for `inconclusive`/`cancelled` runs — so mobile functional tests get zero independent quality check today.
17. **Pass threshold (0.5) is hardcoded**, not environment-configurable — inconsistent with almost every other tunable in this codebase.
18. **No schema enforcement on the judge's own output** — the adapter deliberately omits a response schema and relies on DeepEval's internal fallback-to-plain-text parsing, explicitly flagged in code comments as fragile to future library upgrades.
19. **The actual final prompt DeepEval sends to the judge model is not visible in this codebase** — it lives inside the pinned `deepeval==3.3.9` package, assembled from the `evaluation_steps` this code supplies. Full prompt auditability requires inspecting the installed package.
20. **Same 60-step blind spot applies to GEval too** — for a long run, the "independent" score can be based on under 1% of what the agent actually did.
21. **No re-run / escalation on a low score.** If GEval scores a run poorly, nothing automatically retries it, re-routes it to a more capable model, or queues it for human review — the low score just sits in the database.

### E. UI testing (Visual Judge) — currently only reachable through the SOW/design-audit pipeline, not through the new-test flow
22. **UI/visual regression checks exist (`visual_judge.py`) but aren't a first-class "new test" option today** — they're only triggered via the separate Visual Audit API, not from the same test-creation surface as functional tests. Splitting test creation in two (Part 2, Phase 1) is what surfaces this properly.
23. **The vision pass has no `max_tokens` override** (stays at the router's 4096 default) — on a busy page with many structural differences, findings could be truncated with no truncation-detection or retry.
24. **The deterministic-keyword filter that drops color/spacing findings from the vision pass is a naive substring match** — a legitimately structural finding that happens to mention "padding" or "spacing" in its text could be incorrectly dropped as a duplicate.

### F. No regression safety net for the AI itself
25. **No prompt-eval harness.** There's no golden set of known goals/test cases with expected outcomes used to regression-test changes to any of these prompts — a prompt edit could silently make things worse with nothing to catch it.
26. **Skill identity is exact-hash based**, not fuzzy — a one-word rewording of a goal creates an entirely new "skill" with no history linking it to the original, making flakiness/regression tracking over time difficult.

---

## Part 2 — Implementation Checklist for Reliable, Complete Testing

Organized in delivery order. Each phase is buildable independently, but later phases assume earlier ones exist.

### Phase 1 — Split test creation into two distinct types (fixes A.1–A.6, E.22)
- [ ] Remove the single free-text goal box as the entry point to "New test." Replace it with a choice between two dedicated flows: **UI Test** and **Functional Test**. No merged or "quick" mode.
- [ ] **UI Test flow** (backed by the existing `visual_judge.judge()` pipeline): fields are `reference design` (upload, Figma import, or a saved reference), `target URL/page`, and an optional `linked requirement`. No step scripting — this flow is a comparison, not a workflow, and should not accept a free-text goal at all.
- [ ] **Functional Test flow** (backed by the Hands browser agent + GEval): fields are `preconditions`, an ordered list of atomic `steps`, one or more `expected results`, optional `test data`, and an optional `linked requirement`. This replaces the old single-sentence goal — steps compile into the agent's task internally, but the user always authors them as discrete, orderable items, never one blob of prose.
- [ ] Allow `test_data` on functional tests to hold multiple named data sets so one test case can run parameterized (data-driven) across several inputs.
- [ ] Add a `test_type: happy | negative | edge` flag on functional tests so invalid-input and boundary tests are explicit, trackable test cases rather than ad hoc goals someone remembers to write.
- [ ] Both flows write to the same underlying `linked requirement` field so coverage can be reported per requirement across UI and functional tests together (Phase 5).

### Phase 2 — Execution reliability hardening (fixes B.7–B.12)
- [ ] Verify `gemini-3.5-flash` and `google/gemma-4-26b-a4b-it:free` are real, currently-served model IDs with their providers; replace or remove if not. Add a startup/health-check probe that logs a hard warning if a configured model ID 404s.
- [ ] Fix or remove the no-op Anthropic/OpenAI key rotation (`_with_key_rotation`) — either implement real rotation compatible with browser-use's validation, or update the docs/UI so operators don't believe multi-key configs provide redundancy they don't have.
- [ ] Add configurable browser engine support (Firefox/WebKit via Playwright) behind an environment flag, and let a functional test specify which browser(s) to run against.
- [ ] Make viewport size configurable per functional test (desktop default kept, but allow a mobile/tablet viewport option) to catch responsive-layout functional breaks.
- [ ] Add a lightweight prompt-injection guard: flag/log when the agent's next action diverges sharply from the test's stated steps (e.g., navigating to an unrelated domain not in scope) before executing it, rather than trusting page content unconditionally.
- [ ] Re-confirm `allow_unrestricted_domains=True` is still the intended posture for ad-hoc login runs; if not, add an explicit allowlist step during functional-test setup.

### Phase 3 — Full-run observability (fixes C.13–C.14)
- [ ] Replace the flat "first 60 steps" cap in `generate_narrative_summary()` and `ai_eval.evaluate_run()` with a smarter sample: always include the first 10 steps, the last 30 steps, and every step marked `failed`/`error`/`ai_scoped`-with-anomaly, up to a total cap (e.g. 100 steps) — so a late-run failure is never invisible.
- [ ] Log a visible marker when truncation actually occurs ("step log truncated: N of M steps shown") so a reviewer knows the summary/score might be incomplete, instead of silently proceeding.
- [ ] Fix the silent `except Exception: fail_shot_url = None` screenshot-capture paths to log a warning with the run ID whenever a screenshot is lost.
- [ ] Store the full, untruncated step log permanently (even if the summary/score only samples from it) so a human can always go back and inspect exactly what happened, step by step.

### Phase 4 — Harden the second AI check (GEval) for Functional Tests (fixes D.15–D.21)
- [ ] **Make GEval gate the run status.** Change `_persist_result()` so a functional test run only shows `passed` when BOTH the agent's self-report is `passed` AND `eval_score >= threshold`; otherwise set a distinct status such as `needs_review` (not silently `passed`, not silently `failed`) so a human decides.
- [ ] **Enable GEval for Android too.** Build an Android-appropriate version of `evaluate_run()` using the accessibility-tree action log instead of browser events, so mobile functional tests get the same independent check web tests do.
- [ ] **Make the pass threshold environment-configurable** (`VIBE_TEST_EVAL_THRESHOLD`, default 0.5) to match the pattern used for every other tunable in this codebase.
- [ ] **Add an explicit response schema** to the GEval judge call instead of relying on DeepEval's plain-text fallback parsing; pin and test against the exact `deepeval` version in CI so a library upgrade can't silently break scoring.
- [ ] **Pull and document the actual final judge prompt** DeepEval assembles from the `evaluation_steps` (inspect the installed package once, save a copy in-repo) so the full prompt chain is auditable without needing to read a third-party library each time.
- [ ] **Extend GEval's step sampling to match Phase 3's smarter sampling** (not just the first 60) — the independent check should see the same "first + last + anomalies" window as the summary, not a narrower one.
- [ ] **Add a second, complementary judge pass** that compares a final-state screenshot against the functional test's `expected results` (visual/textual confirmation), separate from the existing action-trace judge — two independent signals (did the actions match the steps, did the end state match the expectation) catch different failure modes than one score alone.
- [ ] **Add escalation on a low score**: automatically flag the run for human review, and optionally auto-retry once on a more capable model before giving up, rather than just storing the number.

### Phase 5 — Harden UI Testing as its own first-class flow (fixes E.22–E.24)
- [ ] Expose `visual_judge.judge()` directly from the new "UI Test" creation flow (Phase 1) rather than only through the separate Visual Audit/SOW-design-review surface, so UI tests are created and tracked the same way functional tests are.
- [ ] Give the vision pass its own configurable `max_tokens` (independent of the router's 4096 default) and detect/retry on likely truncation (e.g., a findings array that stops mid-object).
- [ ] Replace the naive substring keyword filter (color/spacing dedup between the pixel-diff and vision passes) with a more precise check — e.g., only drop a vision finding if its `element` also matches a pixel-diff-flagged region, not just because its text contains a keyword.
- [ ] Add the same "linked requirement" and coverage tracking to UI tests as functional tests get (Phase 6), so visual coverage is reportable per requirement too.

### Phase 6 — Result trust & reporting (fixes A.4, D.15)
- [ ] Add a coverage report view: requirement/checkpoint → linked UI test(s) and/or functional test(s) → latest status → (for functional tests) GEval score → last-run date, so "is this feature actually fully tested — visually and functionally" becomes answerable at a glance.
- [ ] Surface `eval_score`/`eval_reason`/`needs_review` prominently in the functional test run detail UI, and surface structural/pixel findings prominently in the UI test run detail — not just as background fields.
- [ ] Track pass-rate history per test (UI and functional separately) to distinguish a genuinely broken feature from a flaky test.

### Phase 7 — Regression safety net for the AI itself (fixes F.25–F.26)
- [ ] Build a small golden-set eval harness: a fixed set of UI tests and functional tests with known-correct outcomes, re-run automatically whenever any prompt in `ai_runner.py`, `ai_eval.py`, `visual_judge.py`, or the message-context guidance text changes, to catch regressions before they ship.
- [ ] Add fuzzy/semantic matching (not just exact-hash) when deciding whether a new functional test should reuse an existing recorded skill, so minor rewordings don't fragment history and flakiness tracking.
- [ ] Track and surface a per-test flakiness rate (pass/fail variance across repeated runs of the same test) so genuinely unreliable tests are visible, not just genuinely broken features.

### Phase 8 — Definition of done
Testing can be called reliable and complete once:
- [ ] "New test" offers exactly two entry points — UI Test and Functional Test — with no generic free-text goal box between them.
- [ ] Every functional test has explicit preconditions, ordered steps, and expected results — not just a single free-text sentence.
- [ ] Every UI test and functional test can be linked to a requirement/checkpoint, and a coverage report can show what is and isn't tested, split by test type.
- [ ] Long functional runs are judged (summary + GEval) on a representative sample that always includes the point of failure, not just the first 60 steps.
- [ ] GEval score actually affects the displayed functional-test status (no run can show "passed" while scoring below threshold) and runs on both web and Android.
- [ ] Negative/edge-case functional test types exist alongside happy-path tests.
- [ ] UI tests are created and tracked through the same surface as functional tests, not a separate hidden pipeline.
- [ ] A golden-set regression harness exists and runs whenever agent/eval/visual-judge prompts change.

---

*Generated from direct inspection of `backend/app/services/ai_runner.py`, `backend/app/workers/tasks/ai_execution.py`, `backend/app/services/ai_eval.py`, and `backend/app/services/visual_judge.py`. Cross-reference with `AI_Agent_System_Reference.docx` (Parts 2.1, 2.4, 2.5, 3.1, and Part 6 Tier 1/2/3) for the full system-wide audit this checklist was drawn from.*
