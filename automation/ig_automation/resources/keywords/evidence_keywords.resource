*** Settings ***
Documentation    Evidence capture keywords — HTML snapshot + AI locator suggestion on failure.
...
...              Folder layout (both keyed by suite/test name):
...                test-artifacts/html/{suite_name}/{test_case_name}.html
...                test-artifacts/ai_suggestions/{suite_name}/{test_case_name}.json
...
...              Used internally by video_keywords.resource teardown.
...              Not imported directly in test files.
Library          Collections
Library          String
Library          ../../libs/evidence_capture.py
Library          ../../libs/ai_locator_suggester.py
Resource         ../browser_compat.resource
Variables        ../../resources/variables/config.py


*** Keywords ***

# ═══════════════════════════════════════════════════════════════════
# HTML SNAPSHOT
# ═══════════════════════════════════════════════════════════════════

Capture Test Evidence
    [Documentation]    Captures HTML snapshot for the current test.
    ...                Always call BEFORE Close Context so the page is still live.
    ...
    ...                Saves to:
    ...                  test-artifacts/html/{SUITE NAME}/{TEST NAME}.html
    ...
    ...                Screenshot capture was removed (redundant on top of the
    ...                HTML snapshot + per-test video — video already covers
    ...                what a still screenshot would show, and dropping it saves
    ...                one Node.js round-trip + disk write per test).
    [Arguments]    ${suite_name}=${SUITE NAME}    ${test_name}=${TEST NAME}
    # Run Keyword And Continue On Failure so evidence capture never blocks teardown
    Run Keyword And Continue On Failure
    ...    Capture Html Snapshot    ${suite_name}    ${test_name}


# ═══════════════════════════════════════════════════════════════════
# AI LOCATOR SUGGESTION — on failure only
# ═══════════════════════════════════════════════════════════════════

Analyze Failure With AI
    [Documentation]    If the test status is FAIL, calls Gemini AI to suggest
    ...                alternative locators for the element that caused the failure.
    ...
    ...                Reads HTML from:
    ...                  test-artifacts/html/{SUITE NAME}/{TEST NAME}.html
    ...                Writes suggestions to:
    ...                  test-artifacts/ai_suggestions/{SUITE NAME}/{TEST NAME}.json
    ...
    ...                Failure message is pulled from ${TEST MESSAGE} (RF built-in).
    ...                Wrapped in Run Keyword And Ignore Error — never fails the test.
    [Arguments]    ${suite_name}=${SUITE NAME}    ${test_name}=${TEST NAME}
    IF    '${TEST STATUS}' == 'FAIL'
        ${msg}=    Set Variable    ${TEST MESSAGE}
        Run Keyword And Ignore Error
        ...    Suggest Locators For Failure    ${suite_name}    ${test_name}    ${msg}
    END


# ═══════════════════════════════════════════════════════════════════
# COMPOSITE — called from video_keywords.resource teardown
# ═══════════════════════════════════════════════════════════════════

Capture Evidence And Analyze Failure
    [Documentation]    Master evidence keyword. Call in Test Teardown BEFORE
    ...                Close Context.
    ...
    ...                Order:
    ...                  1. Capture HTML snapshot (always — pass & fail)
    ...                  2. If FAIL → call Gemini AI for locator suggestions
    ...
    ...                Both steps are wrapped in ignore/continue-on-failure so
    ...                evidence capture never adds a failure to a passing test,
    ...                and never blocks teardown on an already-failing test.
    [Arguments]    ${suite_name}=${SUITE NAME}    ${test_name}=${TEST NAME}
    Capture Test Evidence       ${suite_name}    ${test_name}
    Analyze Failure With AI     ${suite_name}    ${test_name}
