*** Settings ***
Documentation    Candidates page — Smoke, Regression, Search, Dropdown,
...              Sort, Filter, Modal and Security test coverage.
...              Strategy: Explicit waits only. Zero Sleep() calls.
...              Each test is independent via Test Setup navigation.
Library          JSONLibrary
Resource         ../../resources/browser_compat.resource
Library          ../../libs/hopscotch_client.py
Resource         ../../resources/keywords/login_keywords.resource
Resource         ../../resources/keywords/video_keywords.resource
Resource         ../../resources/keywords/candidate_page.resource
Variables        ../../resources/variables/config.py
Variables        ../../pages/candidates_page.py

Suite Setup      Open InterviewGod Suite
Suite Teardown   Close Browser

# Every test opens a fresh recorded context, navigates to /candidates, and
# closes any open modal. Open Test With Recording must come first so the
# Playwright context (and its video recording) is ready before navigation.
Test Setup       Run Keywords
...              Open Test With Recording    AND
...              Navigate To Candidates Page    AND
...              Close Modal If Open

# Save video first, then take a screenshot on failure for extra evidence.
Test Teardown    Run Keywords
...              Close Test And Save Recording    AND
...              Run Keyword If Test Failed    Capture Page Screenshot


*** Variables ***
# ── Search test data ───────────────────────────────────────────
${VALID_SEARCH_NAME}        Arun
${NONEXISTENT_SEARCH}       ZZZZNONEXISTENT_99999_XYZ
${SQL_INJECTION}            '; DROP TABLE candidates; --
${XSS_PAYLOAD}              <script>alert('xss')</script>
${SPECIAL_CHARS}            !@#$%^&*()
${EMOJI_CHAR}               😊
${NUMERIC_SEARCH}           12345

# ── Add Candidate test data ────────────────────────────────────
${TEST_CANDIDATE_NAME}      AutoTest Robot
${TEST_CANDIDATE_EMAIL}     autotest_robot_${RANDOM_INT}@test.com
${TEST_CANDIDATE_PHONE}     9876543210

# ── Expected page text ────────────────────────────────────────
${COUNT_PATTERN}            ^\\d+ Candidates$


*** Test Cases ***

# ══════════════════════════════════════════════════════════════════════════════
# TC_CAND — PAGE-LEVEL SMOKE & LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

TC_CAND_001_Page_Loads_Successfully
    [Documentation]    Verify all critical page elements are visible after load.
    [Tags]             smoke    happy_path    page_load
    Wait Until Element Is Visible    ${PAGE_HEADING}           timeout=15s
    Element Should Be Visible        ${IMPORT_FROM_EXCEL_BTN}
    Element Should Be Visible        ${ADD_CANDIDATES_BTN}
    Element Should Be Visible        ${SEARCH_INPUT}
    Element Should Be Visible        ${ALL_JOBS_DROPDOWN_BTN}
    Element Should Be Visible        ${SORT_BY_BTN}
    Element Should Be Visible        ${FILTER_BY_BTN}
    Page Should Contain              Candidates


TC_CAND_002_Candidate_Count_Label_Displays_And_Has_Number
    [Documentation]    Verify the count label shows a numeric count e.g. "1126 Candidates".
    ...                Uses regex match — not a hardcoded number — so it survives data changes.
    [Tags]             smoke    happy_path
    ${count_text}=    Get Candidate Count Label Text
    Should Match Regexp    ${count_text}    ${COUNT_PATTERN}


TC_CAND_003_Candidate_Cards_Show_Required_Fields
    [Documentation]    Every candidate card must expose Job, Recent Evaluation,
    ...                Status, and Score labels. Verified on the first card only
    ...                to keep the test fast and deterministic.
    [Tags]             smoke    happy_path
    Wait Until Element Is Visible    ${VIEW_DETAILS_FIRST}    timeout=15s
    Element Text Should Be Present On Page    Job
    Element Text Should Be Present On Page    Recent Evaluation
    Element Text Should Be Present On Page    Status
    Element Text Should Be Present On Page    Score


TC_CAND_004_Page_URL_Is_Correct
    [Documentation]    Confirm the URL contains /dashboard/candidates after navigation.
    [Tags]             smoke    happy_path
    ${url}=    Get Location
    Should Contain    ${url}    /dashboard/candidates


TC_CAND_005_Page_Heading_Is_Not_Empty
    [Documentation]    The h1 heading must not be blank or whitespace-only.
    [Tags]             smoke    happy_path
    Wait Until Element Is Visible    ${PAGE_HEADING}    timeout=10s
    ${heading}=    Get Text    ${PAGE_HEADING}
    Should Not Be Empty    ${heading}
    Should Be Equal As Strings    ${heading}    Candidates


TC_CAND_006_Select_All_Button_Is_Visible
    [Documentation]    The select-all chevron button below the filter row must
    ...                be present and visible when candidates exist.
    [Tags]             happy_path    layout
    Wait For Candidates List To Load
    Element Should Be Visible    ${SELECT_ALL_BTN}


TC_CAND_007_Import_Excel_Button_Is_Visible_And_Clickable
    [Documentation]    Import from Excel button must be clickable and open modal.
    [Tags]             happy_path    smoke
    Wait Until Element Is Visible    ${IMPORT_FROM_EXCEL_BTN}    timeout=8s
    Element Should Be Enabled        ${IMPORT_FROM_EXCEL_BTN}
    Open Import Excel Modal
    Element Should Be Visible        ${IMPORT_EXCEL_MODAL_TITLE}
    Close Import Excel Modal


TC_CAND_008_Add_Candidates_Button_Is_Visible_And_Opens_Modal
    [Documentation]    Add Candidates button must open the Add Candidate modal.
    [Tags]             happy_path    smoke
    Wait Until Element Is Visible    ${ADD_CANDIDATES_BTN}    timeout=8s
    Element Should Be Enabled        ${ADD_CANDIDATES_BTN}
    Open Add Candidate Modal
    Element Should Be Visible        ${ADD_MODAL_TITLE}
    Close Add Candidate Modal


# ══════════════════════════════════════════════════════════════════════════════
# TC_CAND_NEGATIVE — AUTH & SECURITY
# ══════════════════════════════════════════════════════════════════════════════

TC_CAND_NEG_001_Unauthenticated_User_Cannot_Access_Candidates
    [Documentation]    After deleting cookies the app must redirect away from
    ...                /dashboard/candidates — confirming the route is protected.
    ...                Here we restore the bypass session manually after the check.
    [Tags]             negative    security
    [Teardown]         Run Keywords
    ...                Refresh Session Token    AND
    ...                Navigate To Candidates Page
    Delete All Cookies
    Go To                             ${BASE_URL}dashboard/candidates
    Wait Until Location Does Not Contain    /dashboard/candidates    timeout=12s
    ${location}=    Get Location
    Should Not Contain    ${location}    /dashboard/candidates


# ══════════════════════════════════════════════════════════════════════════════
# TC_SRCH — SEARCH BAR (HAPPY PATH)
# ══════════════════════════════════════════════════════════════════════════════

TC_SRCH_001_Search_Bar_Visible_And_Receives_Focus
    [Documentation]    Search input must be visible and accept keyboard focus.
    [Tags]             smoke    happy_path    search
    Wait Until Element Is Visible    ${SEARCH_INPUT}    timeout=8s
    Click Element                    ${SEARCH_INPUT}
    Element Should Be Focused        ${SEARCH_INPUT}


TC_SRCH_002_Search_With_Valid_Name_Returns_Results
    [Documentation]    Searching an existing candidate name must return at least
    ...                one card with a "View Details" button.
    [Tags]             happy_path    search
    Search For Candidate    ${VALID_SEARCH_NAME}
    Wait Until Page Contains Element    ${VIEW_DETAILS_BTN}    timeout=10s
    ${count}=    Get Candidate Card Count
    Should Be True    ${count} >= 1
    ...    msg=Expected at least 1 result for '${VALID_SEARCH_NAME}', got ${count}


TC_SRCH_003_Search_Results_Update_Dynamically_With_Longer_Term
    [Documentation]    Typing a single letter then extending to a full name must
    ...                produce a count <= the single-letter result (narrowing).
    [Tags]             happy_path    search
    Search For Candidate    v
    ${count_v}=    Get Candidate Card Count
    Search For Candidate    varun
    ${count_varun}=    Get Candidate Card Count
    Should Be True    ${count_varun} <= ${count_v}
    ...    msg=Results for 'varun' (${count_varun}) should be <= results for 'v' (${count_v})


TC_SRCH_004_Search_Is_Case_Insensitive
    [Documentation]    Uppercase and lowercase search of the same name should
    ...                return identical result counts.
    [Tags]             happy_path    search
    Search For Candidate    arun
    ${count_lower}=    Get Candidate Card Count
    Search For Candidate    ARUN
    ${count_upper}=    Get Candidate Card Count
    Should Be Equal As Integers    ${count_lower}    ${count_upper}
    ...    msg=Case-insensitive search failed: lower=${count_lower}, upper=${count_upper}


TC_SRCH_005_Clear_Search_Restores_Full_List
    [Documentation]    After a search, clearing the input must restore the full
    ...                candidate list (same count as before searching).
    [Tags]             happy_path    search
    # Capture baseline
    ${original_count}=    Get Candidate Card Count
    # Search and confirm filtered
    Search For Candidate    ${VALID_SEARCH_NAME}
    ${filtered_count}=    Get Candidate Card Count
    # Clear and confirm restoration
    Search For Candidate    ${EMPTY}
    ${restored_count}=    Get Candidate Card Count
    Should Be Equal As Integers    ${original_count}    ${restored_count}
    ...    msg=Count after clearing (${restored_count}) != original (${original_count})


TC_SRCH_006_Search_Input_Placeholder_Text_Is_Correct
    [Documentation]    The placeholder guides the user — must say "Search candidate name".
    [Tags]             happy_path    search    ui
    Wait Until Element Is Visible    ${SEARCH_INPUT}    timeout=8s
    ${placeholder}=    Get Element Attribute    ${SEARCH_INPUT}    placeholder
    Should Be Equal As Strings    ${placeholder}    Search candidate name


# ══════════════════════════════════════════════════════════════════════════════
# TC_SRCH_NEGATIVE — SEARCH EDGE CASES & SECURITY
# ══════════════════════════════════════════════════════════════════════════════

TC_SRCH_007_Search_With_No_Match_Shows_Zero_Results
    [Documentation]    A random nonsense search must yield 0 candidate cards
    ...                and must NOT show any application error.
    [Tags]             negative    search
    Search For Candidate    ${NONEXISTENT_SEARCH}
    ${count}=    Get Candidate Card Count
    Should Be Equal As Integers    ${count}    0
    Page Should Not Contain    Error
    Page Should Not Contain    500


TC_SRCH_008_Search_With_Only_Whitespace_Handles_Gracefully
    [Documentation]    Searching with only spaces must not crash. The app should
    ...                either show all results or none — not an error state.
    [Tags]             negative    edge_case    search
    Input Text    ${SEARCH_INPUT}    ${SPACE}${SPACE}${SPACE}
    Wait For Candidates List To Load
    Page Should Not Contain    Error
    Page Should Not Contain    500
    Page Should Not Contain    undefined


TC_SRCH_009_Search_With_Special_Characters_No_Crash
    [Documentation]    Special characters must be handled gracefully — no 500,
    ...                no unhandled exception, no stack trace.
    [Tags]             negative    edge_case    search
    Search For Candidate    ${SPECIAL_CHARS}
    Page Should Not Contain    Error
    Page Should Not Contain    500
    Page Should Not Contain    SyntaxError
    Page Should Not Contain    undefined


TC_SRCH_010_Search_With_Numbers_Only_No_Crash
    [Documentation]    Numeric-only input must not produce an error.
    [Tags]             negative    edge_case    search
    Search For Candidate    ${NUMERIC_SEARCH}
    Page Should Not Contain    Error
    Page Should Not Contain    500


TC_SRCH_011_Search_With_SQL_Injection_Is_Safe
    [Documentation]    SQL injection payload must not crash the app or expose
    ...                DB errors. The input is treated as plain text.
    [Tags]             negative    security    search
    Search For Candidate    ${SQL_INJECTION}
    Page Should Not Contain    Error
    Page Should Not Contain    500
    Page Should Not Contain    SQL
    Page Should Not Contain    syntax error
    Page Should Not Contain    ORA-
    Page Should Not Contain    mysql_fetch


TC_SRCH_012_Search_With_XSS_Payload_Not_Executed
    [Documentation]    An XSS payload typed into the search box must be rendered
    ...                as text — the script tag must never execute.
    [Tags]             negative    security    search
    Search For Candidate    ${XSS_PAYLOAD}
    # If XSS executed, an alert dialog would appear. Verify no alert is present.
    ${alert_present}=    Run Keyword And Return Status    Alert Should Be Present    timeout=2s
    Should Not Be True    ${alert_present}    msg=XSS payload triggered a JS alert!
    Page Should Not Contain    Error
    Page Should Not Contain    500


TC_SRCH_013_Search_With_Emoji_No_Crash
    [Documentation]    Unicode emoji input must not crash the page.
    [Tags]             edge_case    search
    Search For Candidate    ${EMOJI_CHAR}
    Page Should Not Contain    Error
    Page Should Not Contain    500
    Page Should Not Contain    undefined


TC_SRCH_014_Search_With_Very_Long_String_No_Crash
    [Documentation]    An extremely long string must not cause layout break
    ...                or server error (boundary test).
    [Tags]             edge_case    search
    ${long_string}=    Evaluate    'A' * 500
    Search For Candidate    ${long_string}
    Page Should Not Contain    Error
    Page Should Not Contain    500
    Element Should Be Visible    ${SEARCH_INPUT}


# ══════════════════════════════════════════════
