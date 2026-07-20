# ig_automation — InterviewGod E2E Test Suite

End-to-end UI test automation for **InterviewGod** (`https://pre-prod.interviewgod.ai/` by
default — see [Environment variables](#environment-variables)). Covers Login, Dashboard,
Jobs, Job Details, and Candidates. This is one of two Robot Framework suites in the
`automation/` folder of the AEP monorepo (the other, `vidya_website_automation/`, tests an
unrelated marketing site and isn't covered here).

> This file is the source of truth for how this suite works. Update it whenever the
> architecture, folder layout, or run flow changes — don't let it drift from the code the
> way the previous version did (see [Known issues](#known-issues-found-2026-07-15)).

## Tech stack

| Layer | Technology |
|---|---|
| Test framework | Robot Framework 7.4.2 |
| Browser automation | `robotframework-browser` 18.9.1 (Playwright under the hood — Chromium/Firefox/WebKit) |
| Language | Python 3.10 (page objects, libraries) + Robot Framework DSL (`.robot`/`.resource` files) |
| Auth bypass | Custom `HopscotchClient` library (`libs/hopscotch_client.py`) — skips the UI login form via an API call |
| Evidence capture | `libs/evidence_capture.py` — HTML snapshot per test (see [Known issues](#known-issues-found-2026-07-15) — screenshot capture exists in code but is no longer wired in) |
| Per-test video | `libs/video_recorder.py` — one `.webm` per test, Playwright/Browser library handles the actual capture |
| AI locator healing | Google Gemini via `google-genai`, **offline/post-run only** (`libs/ai_locator_suggester.py`) |
| Human-in-the-loop apply | `libs/hitl_manager.py` — patches an approved AI suggestion into the page object + `resources/locators.resource` |
| Linting | `robotframework-robocop` 6.11.0 (`robot.toml`) |
| CI/CD | GitHub Actions (`.github/workflows/ci.yml`) — scheduled daily + manual dispatch |

**Selenium has been fully removed.** Nothing here should import `selenium`,
`seleniumlibrary`, `webdriver`, or `webdriver-manager`. (One stale file still does —
flagged below.)

## How this fits into the AEP monorepo

This suite is a standalone Robot Framework project — it does **not** currently receive any
calls from, or report results back into, the `dashboard/backend` FastAPI service. The
dashboard's "Execute" and "Reports" pages track *dashboard-native* test suite/run/result
records in Postgres; there is no code path in `dashboard/backend` that shells out to `robot`
or reads from this folder's `test-artifacts/`. Treat the dashboard and this suite as two
separate systems today, not an integrated pipeline — the top-level
[dashboard/README.md](../../dashboard/README.md) describes the intended future integration
("the dashboard can trigger these tests and display their results"), which has not been
built yet. See [Known issues](#known-issues-found-2026-07-15).

## Folder structure

```
automation/ig_automation/
├── .env                          # local secrets — never commit (correctly gitignored)
├── local.args                    # local run argumentfile (see caveat below)
├── requirements.txt
├── robot.toml                    # Robocop lint config
│
├── libs/
│   ├── hopscotch_client.py       # CI/CD login bypass — the ONLY copy actually used
│   ├── ai_locator_suggester.py   # Gemini locator suggestions — offline/post-run only
│   ├── hitl_manager.py           # applies an approved suggestion to a page object
│   ├── evidence_capture.py       # HTML snapshot per test
│   ├── video_recorder.py         # per-test video lifecycle
│   └── driver_factory.py         # stub — Selenium removed, kept to avoid ImportError
│
├── pages/                        # Page Object Model — locators + small Python helpers
│   ├── base_page.py
│   ├── login_page.py
│   ├── dashboard_page.py
│   ├── jobs_page.py
│   ├── job_details_page.py
│   └── candidates_page.py
│
├── resources/
│   ├── browser_compat.resource   # SeleniumLibrary-keyword-name → Browser-library shim
│   ├── locators.resource         # AI-approved locator overrides, patched by hitl_manager
│   ├── keywords/
│   │   ├── login_keywords.resource
│   │   ├── dashboard_keywords.resource
│   │   ├── jobs_keywords.resource
│   │   ├── job_details_keywords.resource
│   │   ├── candidate_page.resource
│   │   ├── evidence_keywords.resource   # HTML capture + enqueue AI analysis on FAIL
│   │   └── video_keywords.resource      # per-test browser context/video teardown
│   └── variables/
│       └── config.py             # env-driven test data/config constants
│
├── tests/
│   ├── login/login_tests.robot
│   ├── dashboard/dashboard_tests.robot
│   ├── dashboard/dashboard_tests2.robot
│   ├── dashboard/dashboard_tests3.robot
│   ├── jobs/jobs_tests.robot
│   ├── job_details/job_details_tests.robot
│   └── candidates/candidates_test.robot
│
├── test_data/                    # JSON fixtures actually used (jobs/dashboard/job_details)
│
├── test-artifacts/               # generated per run
│   ├── html/{suite}/{test}.html                 # evidence snapshot, every test
│   └── ai_suggestions/{suite}/{test}.json        # AI healing queue + suggestions
│
└── .github/workflows/ci.yml
```

## Architecture

### 1. The compatibility shim — `resources/browser_compat.resource`

The project was migrated from SeleniumLibrary to the Browser library (Playwright). Rather
than rewrite every test, `browser_compat.resource` defines SeleniumLibrary-style keyword
names (`Click Element`, `Input Text`, `Wait Until Element Is Visible`, …) as wrappers around
the real Browser library keywords (`Click`, `Fill Text`, `Wait For Elements State`, …).

Every `.robot` and `.resource` file imports this shim — never `Library SeleniumLibrary`.

### 2. Auth bypass — `libs/hopscotch_client.py`

Tests skip the UI login form:

1. `HopscotchClient.bypass_login_and_open_session()` POSTs to `${API_BASE_URL}${BYPASS_ENDPOINT}`
   (`/admin-login-by-api-key`) with `X_API_KEY`.
2. Reads `auth_token` from the response.
3. Opens a Playwright browser (via `BuiltIn().run_keyword(...)`, never the raw Python API —
   see gotcha below), navigates to `${BASE_URL}`.
4. Injects the token as a cookie (`AUTH_COOKIE_NAME`, with `domain=AUTH_COOKIE_DOMAIN` —
   Playwright rejects `add_cookie()` without a domain).
5. Navigates to `/dashboard` — the SPA picks it up and skips login.

Every suite except `login_tests.robot` (which deliberately exercises the real UI login flow,
OTP validation, etc.) uses `Suite Setup    Open InterviewGod With Bypass`.

### 3. Evidence capture — HTML only, per test

`evidence_keywords.resource` → `Capture Evidence And Analyze Failure` runs in every suite's
teardown (via `video_keywords.resource`), **before** the browser context closes:

1. `Capture Test Evidence` — always saves an HTML snapshot to
   `test-artifacts/html/{suite}/{test}.html`. (Screenshot capture is implemented in
   `evidence_capture.py` but is **no longer called** — see
   [Known issues](#known-issues-found-2026-07-15).)
2. `Analyze Failure With AI` — only on `FAIL`, hands off to the AI locator suggester below.

### 4. Per-test video — `libs/video_recorder.py` + `video_keywords.resource`

One browser context per test (not per suite), so each test gets its own `.webm` under
`test-artifacts/videos/{suite}/`. This is the context-per-test architecture referenced
elsewhere in this repo's history.

### 5. AI locator healing — `libs/ai_locator_suggester.py` + `libs/hitl_manager.py`

A **suggestion** system, not auto-healing. Three deliberate gates: **AI proposes → a human
approves → `hitl_manager` applies → you rerun the tests.** Nothing here reruns tests or
edits a page object on its own.

1. On `TEST STATUS == FAIL`, `Suggest Locators For Failure` runs **synchronously and does no
   network I/O** — it just extracts the failed locator from the RF failure message (regex)
   and writes `test-artifacts/ai_suggestions/{suite}/{test}.json` with `status: "queued"`.
   Setup/navigation failures (e.g. a `page.goto` timeout) have no locator to heal and are
   skipped — nothing is queued for them.
2. **After** the run, process the queue offline:
   ```bash
   python libs/ai_locator_suggester.py
   ```
   This calls Gemini once per queued record, sequentially (bounded concurrency — one request
   at a time), and rewrites each file with `status: "pending_review"` plus 2–3 suggested
   locators (confidence rating, `approved: null` each). Key rotation: reads
   `GEMINI_API_KEYS` (comma-separated) or `GEMINI_API_KEY_1`, `_2`, …; rotates on HTTP 429,
   retries with backoff on timeout/server error, gives up after `AI_MAX_ATTEMPTS` (default 3)
   per record. All failures are logged and swallowed — this step never throws.
3. A human opens the JSON file and sets `"approved": true` on the correct suggestion.
4. `python libs/hitl_manager.py apply` patches the approved locator into the relevant
   `pages/*.py` class attribute and appends an entry to `resources/locators.resource`
   (currently empty — no suggestions have been approved yet in this repo).
5. **You rerun the tests manually.** Nothing here reruns automatically.

> **Why AI calls stay out of the live run:** an earlier version of this library spawned an
> unbounded background thread per failure that streamed from Gemini for up to 60 seconds
> *during* the run. Under a failure cascade this produced dozens of concurrent long-lived
> threads that saturated the network and starved Playwright, so the next suite's `page.goto`
> timed out — one failure cascaded into a whole-suite meltdown. The current
> `suggest_locators_for_failure()` is enqueue-only and network-free by design; keep it that
> way. (The module's top-of-file docstring still describes the old background-thread
> behavior — that comment is stale, not the actual behavior; see
> [Known issues](#known-issues-found-2026-07-15).)

Known gap (unchanged from before): there is no automatic re-validation of a suggested
locator against the live DOM before a human reviews it. Treat confidence as a hint, not
proof.

## Locator strategy (strict — follow every time)

**Priority order:**
1. `data-testid` attribute → `[data-testid='element-name']`
2. ID attribute → `id=element-id` or `#element-id`
3. CSS selector → `css=.class-name` or `tag[attr='value']`
4. Relative XPath → `xpath=//tag[@attribute='value']`

**Never:** absolute XPath (`/html/body/div[1]/...`), index-based XPath (`//div[3]`), or
unnormalized text XPath (`//div[text()='Label']` — breaks on whitespace; use
`normalize-space()`).

**Playwright strict mode is always on** — `Wait For Elements State` fails immediately if a
locator matches more than one element. Confirm any new locator resolves to exactly one node
in DevTools (`$x("//your/xpath").length === 1`) before using it.

**Dual-render UI problem:** this Tailwind app renders many elements twice — once in a
`lg:hidden` (mobile) container, once in `hidden lg:flex` (desktop). Both exist in the DOM
simultaneously. Exclude the mobile copy: `not(ancestor::div[contains(@class,'lg:hidden')])`
(see `pages/dashboard_page.py` for real examples).

Locators live only in `pages/*.py` as class-level constants — never inline in `.robot` or
`.resource` files.

## Test case rules

- Test case **bodies are frozen** — only add new test cases, don't edit existing steps.
  Infrastructure changes belong in page objects, keywords, or the compat shim.
- No `Sleep` — use `Wait Until Element Is Visible` / `Wait Until Page Contains Element` with
  explicit timeouts (default `${Timeout}` from `config.py`, 15s).
- No hardcoded data in assertions — assert format/type/presence
  (`Should Match Regexp ... ^[0-9]+$`), never a specific count that depends on live DB state.
- Every test case needs `[Tags]` (from: `smoke`, `regression`, `boundary`, `negative`,
  `edge`, `sow`, `performance`, `ci-safe`) and a `[Documentation]` line.
- Every suite except `login_tests.robot` uses `Suite Setup Open InterviewGod With Bypass` /
  `Suite Teardown Close Browser`.

## Running tests

```bash
# One-time setup
pip install -r requirements.txt
rfbrowser init                     # downloads Playwright browser binaries — required

# Local smoke run (dashboard only)
robot --outputdir results --pythonpath . --variable BROWSER:chromium --loglevel DEBUG tests/dashboard/dashboard_tests.robot

# "Full suite" via argumentfile — see caveat below, this does NOT run every suite
robot --argumentfile local.args

# Generate AI locator suggestions (OPTIONAL, run AFTER the robot run, never during)
python libs/ai_locator_suggester.py
```

> **`local.args` currently only lists** `dashboard_tests.robot`, `jobs_tests.robot`, and
> `job_details_tests.robot`. It does **not** include `login_tests.robot`,
> `candidates_test.robot`, `dashboard_tests2.robot`, or `dashboard_tests3.robot`. Running
> `robot --argumentfile local.args` silently skips those four files — see
> [Known issues](#known-issues-found-2026-07-15).

CI (`.github/workflows/ci.yml`) runs on a daily schedule (3:30 AM UTC) and on manual
dispatch, using the same `local.args` list, filtered to `--include ci-safe`
(`login_tests.robot` has no `ci-safe`-tagged cases, so it's excluded there too, on top of
already being absent from `local.args`).

## Environment variables

Set in `.env` for local runs (gitignored, not committed); set as GitHub Actions secrets in CI.

| Variable | Description | Default in `config.py` |
|---|---|---|
| `BASE_URL` | App front-end URL | `https://pre-prod.interviewgod.ai/` |
| `API_BASE_URL` | Backend API base URL (no trailing slash) | *(required, no default)* |
| `BYPASS_ENDPOINT` | Login bypass endpoint | `/admin-login-by-api-key` |
| `X_API_KEY` | Secret API key for bypass | *(required, no default)* |
| `AUTH_COOKIE_NAME` | Cookie name seen in DevTools | `authToken` |
| `AUTH_COOKIE_DOMAIN` | Cookie domain — hostname only | `pre-prod.interviewgod.ai` |
| `TEST_EMAIL` / `TEST_OTP` | Test account credentials (bypass + UI login suite) | — |
| `BROWSER` | Browser type | `chrome` (local) / `headlesschrome` (CI) |
| `VIEWPORT_WIDTH` / `VIEWPORT_HEIGHT` | Browser viewport | `1280` / `720` |

AI locator healing (only needed for the offline pass):

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEYS` | Comma-separated Gemini keys (or `GEMINI_API_KEY_1`, `_2`, …), rotated on 429 | — |
| `GEMINI_MODEL` | Model name | `gemini-2.5-flash-lite` |
| `GEMINI_TIMEOUT` | Per-request timeout in seconds | `60` |
| `AI_MAX_OUTPUT_TOKENS` | Cap on generated tokens | `2048` |
| `AI_HTML_MAX_CHARS` | HTML trimmed to this before sending | `12000` |
| `AI_MAX_ATTEMPTS` | Max attempts per queued record | `3` |

> `gemini-2.0-flash` was shut down 2026-06-01 — use `gemini-2.5-flash-lite` or
> `gemini-2.5-flash`.

## Common errors and fixes

| Error | Root cause | Fix |
|---|---|---|
| `'str' object has no attribute 'name'` | Calling `browser_lib.new_browser("chromium")` directly from Python | Use `BuiltIn().run_keyword("New Browser", "chromium", False)` |
| `Cookie should have a url or a domain/path pair` | `add_cookie()` called without `domain=` | Pass `domain=config["cookie_domain"]` |
| `Keyword expected 0 to 2 non-named arguments, got 4` | Extra positional args passed to `New Browser` | Use `Set Viewport Size` for layout instead |
| `Strict mode violation: … resolved to X elements` | Locator matches multiple elements | Narrow with sibling navigation or `not(ancestor::...)` |
| `rfbrowser init not run` | Playwright binaries not downloaded | Run `rfbrowser init` once after install |
| `AUTH_COOKIE_DOMAIN contains https://` | Domain has a URL prefix | Set to hostname only, e.g. `pre-prod.interviewgod.ai` |
| `SeleniumLibrary not found` | A file imports `Library SeleniumLibrary` | Replace with `Resource .../browser_compat.resource` |
| AI calls fail instantly with `ReadTimeout` | `google-genai` timeout is milliseconds; a bare `60` means 60ms | `ai_locator_suggester.py` already converts `GEMINI_TIMEOUT` seconds → ms — don't pass ms directly |

## Known issues (found 2026-07-15)

Found while writing this doc — none fixed, flagging for a human decision:

1. ~~Orphaned, stale, Selenium-based `hopscotch_client.py` at the project root~~ — **fixed
   2026-07-15**: deleted. It was a stale duplicate of `libs/hopscotch_client.py` that still
   imported Selenium, unused by any test/keyword file.
2. **`resources/variables/config.py` references a `test_files/` directory that doesn't
   exist** — `VALID_PDF_PATH`, `LARGE_PDF_PATH`, `INVALID_FORMAT_PATH`, `VALID_EXCEL_PATH`,
   `VALID_XLS_PATH`, `LARGE_EXCEL_PATH`, `INVALID_EXCEL_FORMAT` all point at
   `${CURDIR}/../test_files/...`. Only `test_data/*.json` actually exists in the repo. Any
   test case that references these constants for file-upload scenarios will fail with a
   file-not-found error the moment it's exercised.
3. **`local.args` doesn't run the full suite it implies.** It lists only
   `dashboard_tests.robot`, `jobs_tests.robot`, `job_details_tests.robot` — silently omitting
   `login_tests.robot`, `candidates_test.robot`, `dashboard_tests2.robot`, and
   `dashboard_tests3.robot`. Anyone running `robot --argumentfile local.args` expecting full
   coverage (including CI, which uses the same file) is not exercising Candidates or Login
   at all, and only exercising a third of the Dashboard suite.
4. **Stale docstring in `libs/ai_locator_suggester.py`** (top of file) still describes the
   old, since-fixed "background daemon thread" behavior ("AI runs in a background daemon
   thread — teardown returns immediately"). The actual `suggest_locators_for_failure()`
   implementation is correct (enqueue-only, no network I/O) — only the comment is wrong, but
   it directly contradicts the file's own inline documentation lower down and could mislead
   the next person to touch this file into thinking the dangerous behavior is still present
   (or reintroducing it). Worth a one-line comment fix.
5. **Evidence capture no longer takes screenshots**, only HTML snapshots — screenshot logic
   still exists in `evidence_capture.py` but the composite keyword that all suites actually
   call (`Capture Test Evidence`) stopped invoking it (see the inline comment in
   `evidence_keywords.resource` explaining the removal as intentional/redundant with video).
   This doc now reflects that; if the previous parent-level project doc you may have seen
   elsewhere still says "HTML + screenshot snapshot on every test," that line is stale.
6. **No integration between this suite and the dashboard yet**, despite the top-level
   `dashboard/README.md` describing one ("the dashboard can trigger these tests and display
   their results"). See [How this fits into the AEP monorepo](#how-this-fits-into-the-aep-monorepo).
7. **Default `BASE_URL`/`AUTH_COOKIE_DOMAIN` point at `pre-prod.interviewgod.ai`**, not
   `dev.interviewgod.ai`. Not necessarily wrong (env vars override in CI/local `.env`), but
   worth confirming this is the environment you actually intend to target by default.
