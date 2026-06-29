*** Settings ***
Documentation    Per-test video recording lifecycle keywords.
...
...              Usage in every test suite that needs video + evidence capture:
...
...                Suite Setup      Open InterviewGod Suite
...                Suite Teardown   Close Browser
...                Test Setup       Open Test With Recording
...                Test Teardown    Close Test And Save Recording
...
...              This gives each test its own Playwright context so video is
...              scoped per-test, not per-suite.
...
...              Evidence folder structure (all keyed by test name):
...                test-artifacts/videos/{test-name}/latest.webm
...                test-artifacts/html/{test-name}/snapshot.html
...                test-artifacts/html/{test-name}/snapshot.png
...                test-artifacts/ai_suggestions/{test-name}/suggestions.json
Library          Collections
Library          String
Library          ../../libs/video_recorder.py
Library          ../../libs/hopscotch_client.py
Resource         ../browser_compat.resource
Resource         evidence_keywords.resource
Variables        ../../resources/variables/config.py


*** Keywords ***

# ═══════════════════════════════════════════════════════════════════
# TEST SETUP — open a fresh context with video recording enabled
# ═══════════════════════════════════════════════════════════════════

Open Test With Recording
    [Documentation]    Test Setup keyword.
    ...                1. Creates test-artifacts/videos/{test_name}/ folder.
    ...                2. Opens a new Playwright context with recordVideo enabled.
    ...                3. Opens a new page and injects the cached auth cookie.
    ...                4. Navigates to /dashboard, confirms the session is live.
    ...
    ...                Requires Open InterviewGod Suite to have been called in
    ...                Suite Setup (which fetches and caches the auth token).
    [Tags]    ci-safe
    # Step 1 — prepare the per-test video folder (test-artifacts/videos/{test}/)
    ${video_dir}=    Prepare Video Dir    ${SUITE NAME}    ${TEST NAME}
    # Step 2 — open context with video recording + correct viewport
    New Context
    ...    recordVideo={'dir': '${video_dir}'}
    ...    viewport={'width': ${VIEWPORT_WIDTH}, 'height': ${VIEWPORT_HEIGHT}}
    # Step 3 — raise navigation timeout before page load.
    # RF Browser's built-in default is 10 s; the SPA regularly exceeds that.
    # 30 s matches Playwright's own default and gives the app room to respond.
    Set Browser Timeout    30s
    # Step 4 — open page and inject cached auth token (no re-login needed)
    New Page    ${BASE_URL}
    Inject Stored Token


# ═══════════════════════════════════════════════════════════════════
# TEST TEARDOWN — capture evidence → close context → save video
# ═══════════════════════════════════════════════════════════════════

Close Test And Save Recording
    [Documentation]    Test Teardown keyword.
    ...                Order is critical — must happen BEFORE Close Context:
    ...                  1. Capture HTML snapshot + screenshot (always).
    ...                  2. If FAIL → call Gemini AI for locator suggestions.
    ...                After evidence is captured:
    ...                  3. Close Playwright context (flushes video to disk).
    ...                  4. Rename UUID .webm → latest.webm.
    ...
    ...                Always runs even on test failure so all evidence is captured
    ...                for both passing and failing tests.
    [Tags]    ci-safe
    # ── Step 1 & 2: capture evidence BEFORE closing the page ──────────────────
    # Every step below is fault-isolated: a teardown problem (locked video file,
    # already-closed page, etc.) must NEVER turn a passing test red or corrupt the
    # next test. Pass SUITE NAME and TEST NAME explicitly so evidence/AI folders
    # are keyed correctly (suite folder, then test file).
    ${ok}    ${video_dir}=    Run Keyword And Ignore Error    Get Last Video Dir
    ${video_dir}=    Set Variable If    '${ok}' == 'PASS'    ${video_dir}    ${EMPTY}
    Run Keyword And Ignore Error
    ...    Capture Evidence And Analyze Failure    ${SUITE NAME}    ${TEST NAME}
    # ── Step 3: close context — Playwright writes the video file after this ────
    Run Keyword And Ignore Error    Close Context
    # ── Step 4: rename Playwright's page@<hash>.webm → {test}.webm ─────────────
    Run Keyword And Ignore Error    Finalize Video    ${video_dir}
