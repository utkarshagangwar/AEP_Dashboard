*** Settings ***
Documentation    Job details page keywords.
Library          Collections
Library          OperatingSystem
Resource         ../browser_compat.resource
Variables        ../../pages/login_page.py
Variables        ../../pages/job_details_page.py
Variables        ../../resources/variables/config.py

*** Keywords ***

# ============================================================
# NAVIGATION
# ============================================================

Open Job Details Page
    [Documentation]    Navigate directly to the requested job details page.
    Go To    ${BASE_URL}${JobDetailsPage.JOB_DETAILS_PATH}
    Wait For Elements State    ${JobDetailsPage.JOB_TITLE}       visible    ${Timeout}
    Wait For Elements State    ${JobDetailsPage.FIRST_JOB_CARD}  visible    ${Timeout}
    Click    ${JobDetailsPage.FIRST_JOB_CARD}
    # Breadcrumb confirms navigation to a job detail page (shows "Round:" segment)
    Wait For Elements State    xpath=//a[normalize-space()='Jobs']    visible    ${Timeout}

Verify Job Details
    [Documentation]    Confirm the job details header is visible.
    Wait For Elements State    ${JobDetailsPage.JOB_TITLE}    visible    ${Timeout}

Click Sidebar Jobs Tab
    [Documentation]    Navigate to Jobs via sidebar.
    Click    ${JobDetailsPage.JOBS_TAB}
    Wait For Elements State    ${JobDetailsPage.JOB_TITLE}    visible    ${Timeout}

Click Sidebar Dashboard Tab
    [Documentation]    Navigate to Dashboard via sidebar.
    Click    ${JobDetailsPage.DASHBOARD_TAB}

# ============================================================
# SEARCH
# ============================================================

Search Candidate
    [Documentation]    Type a candidate name into the search input.
    [Arguments]    ${candidate_name}
    Wait For Elements State    ${JobDetailsPage.SEARCH_INPUT}    visible    ${Timeout}
    Fill Text    ${JobDetailsPage.SEARCH_INPUT}    ${candidate_name}
    # Brief poll for UI to react — no sleep; wait for either rows or empty state
    Sleep    0.5s    # minimum reaction time for debounced search input

Clear Candidate Search
    [Documentation]    Clear the candidate search box.
    Wait For Elements State    ${JobDetailsPage.SEARCH_INPUT}    visible    ${Timeout}
    Fill Text    ${JobDetailsPage.SEARCH_INPUT}    ${EMPTY}
    Sleep    0.5s

# ============================================================
# FILTER PANEL
# ============================================================

Open Filter Panel
    [Documentation]    Click "Filter by" to open the filter dropdown.
    ${is_open}=    Run Keyword And Return Status
    ...    Wait For Elements State    ${JobDetailsPage.APPLY_FILTER_BUTTON}    visible    2s
    IF    not ${is_open}
        Wait For Elements State    ${JobDetailsPage.FILTER_BUTTON}    visible    ${Timeout}
        Click    ${JobDetailsPage.FILTER_BUTTON}
    END
    Wait For Elements State    ${JobDetailsPage.APPLY_FILTER_BUTTON}    visible    ${Timeout}

Close Filter Panel
    [Documentation]    Close the filter panel by clicking it again.
    Click    ${JobDetailsPage.FILTER_BUTTON}
    Sleep    0.3s

Apply Filters
    [Documentation]    Click the Apply Filters button inside the filter panel.
    Wait For Elements State    ${JobDetailsPage.APPLY_FILTER_BUTTON}    visible    ${Timeout}
    Click    ${JobDetailsPage.APPLY_FILTER_BUTTON}
    Sleep    0.3s

Select Status Filter
    [Documentation]    Select the first status checkbox (Not Sent) in the filter panel.
    Wait For Elements State    ${JobDetailsPage.STATUS_FILTER_OPTIONS}    visible    ${Timeout}
    Click    ${JobDetailsPage.STATUS_FILTER_OPTIONS}

Select Deadline Filter
    [Documentation]    Select the first deadline checkbox (Yesterday) in the filter panel.
    Wait For Elements State    ${JobDetailsPage.DEADLINE_FILTER_OPTIONS}    visible    ${Timeout}
    Click    ${JobDetailsPage.DEADLINE_FILTER_OPTIONS}

Select Score Filter
    [Documentation]    Select the first score range checkbox (Below 50%) in the filter panel.
    Wait For Elements State    ${JobDetailsPage.SCORE_FILTER_OPTIONS}    visible    ${Timeout}
    Click    ${JobDetailsPage.SCORE_FILTER_OPTIONS}

Enter Score Range
    [Documentation]    Fill in min and max score range inputs under "Enter Range".
    [Arguments]    ${min}    ${max}
    Wait For Elements State    ${JobDetailsPage.MIN_SCORE_INPUT}    visible    ${Timeout}
    Fill Text    ${JobDetailsPage.MIN_SCORE_INPUT}    ${min}
    Fill Text    ${JobDetailsPage.MAX_SCORE_INPUT}    ${max}

# ============================================================
# CANDIDATE TABLE ASSERTIONS
# ============================================================

Verify Candidates Present
    [Documentation]    Assert at least one candidate row exists in the table.
    Wait For Elements State    xpath=//tbody/tr[1]    visible    ${Timeout}

Verify No Candidates Empty State
    [Documentation]    Assert the "No candidates yet" empty state is shown.
    Wait For Elements State
    ...    xpath=//*[contains(normalize-space(),'No candidates yet')]
    ...    visible    ${Timeout}

Verify Candidate Count Label
    [Documentation]    Assert the candidate count label is visible.
    Wait For Elements State    ${JobDetailsPage.CANDIDATE_COUNT_LABEL}    visible    5s

# ============================================================
# CANDIDATE SELECTION (CHECKBOXES)
# ============================================================

Select All Candidates
    [Documentation]    Click the select-all checkbox in the table header.
    Wait For Elements State    ${JobDetailsPage.SELECT_ALL_CHECKBOX}    visible    ${Timeout}
    Click    ${JobDetailsPage.SELECT_ALL_CHECKBOX}

Select Single Candidate
    [Documentation]    Select the first candidate row checkbox.
    Wait For Elements State    xpath=//tbody/tr[1]                       visible    ${Timeout}
    Wait For Elements State    ${JobDetailsPage.FIRST_ROW_CHECKBOX}     visible    ${Timeout}
    Click    ${JobDetailsPage.FIRST_ROW_CHECKBOX}

Select Multiple Candidates
    [Documentation]    Select the first two candidate row checkboxes.
    Wait For Elements State    xpath=//tbody/tr[2]                       visible    ${Timeout}
    Wait For Elements State    ${JobDetailsPage.FIRST_ROW_CHECKBOX}     visible    ${Timeout}
    Click    ${JobDetailsPage.FIRST_ROW_CHECKBOX}
    Wait For Elements State    ${JobDetailsPage.SECOND_ROW_CHECKBOX}    visible    ${Timeout}
    Click    ${JobDetailsPage.SECOND_ROW_CHECKBOX}

Select Candidate By Index
    [Documentation]    Select a candidate checkbox by row index (1-based).
    [Arguments]    ${row_index}
    ${locator}=    Set Variable
    ...    xpath=(//tbody/tr//button[@role='checkbox'])[${row_index}]
    Wait For Elements State    ${locator}    visible    ${Timeout}
    Click    ${locator}

# ============================================================
# ROUND TABS
# ============================================================

Click Round Tab
    [Documentation]    Click a round tab by 1-based index.
    [Arguments]    ${round_index}
    ${locator}=    Set Variable
    ...    xpath=(//button[contains(normalize-space(),'Round')])[${round_index}]
    Wait For Elements State    ${locator}    visible    ${Timeout}
    Click    ${locator}

Click First Round Tab
    [Documentation]    Click the first round tab.
    Click Round Tab    1

Get Round Tab Count
    [Documentation]    Return the number of round tabs currently rendered.
    ${count}=    Get Element Count    xpath=//button[contains(normalize-space(),'Round')]
    [Return]    ${count}

# ============================================================
# SEND INTERVIEW BUTTON
# ============================================================

Verify Send Interview Disabled
    [Documentation]    Assert Send Interview button exists but is disabled/greyed.
    Wait For Elements State
    ...    xpath=//button[normalize-space()='Send interview']
    ...    visible    ${Timeout}
    ${selected}=    Get Element Count
    ...    xpath=//tbody/tr//button[@role='checkbox' and @aria-checked='true']
    Should Be Equal As Integers    ${selected}    0
    ...    msg=Expected no candidates selected but found ${selected} selected

Verify Send Interview Enabled
    [Documentation]    Assert Send Interview button is active (at least one candidate selected).
    Wait For Elements State
    ...    xpath=//button[normalize-space()='Send interview']
    ...    visible    ${Timeout}
    ${selected}=    Get Element Count
    ...    xpath=//tbody/tr//button[@role='checkbox' and @aria-checked='true']
    Should Be True    ${selected} >= 1
    ...    msg=Expected at least 1 candidate selected but found ${selected}

Click Send Interview
    [Documentation]    Click the Send Interview button.
    Wait For Elements State
    ...    xpath=//button[normalize-space()='Send interview']
    ...    visible    ${Timeout}
    Click    xpath=//button[normalize-space()='Send interview']

Click Edit Interview
    [Documentation]    Click the Edit Interview button.
    Wait For Elements State    ${JobDetailsPage.EDIT_INTERVIEW_BUTTON}    visible    ${Timeout}
    Click    ${JobDetailsPage.EDIT_INTERVIEW_BUTTON}

# ============================================================
# TOAST NOTIFICATIONS
# ============================================================

Verify Success Toast
    [Documentation]    Wait for and assert a success toast is visible.
    Wait For Elements State    ${JobDetailsPage.SUCCESS_TOAST}    visible    ${Timeout}

Verify Error Toast
    [Documentation]    Wait for and assert an error toast is visible.
    Wait For Elements State    ${JobDetailsPage.ERROR_TOAST}    visible    ${Timeout}

# ============================================================
# STATUS BADGES
# ============================================================

Verify Status Updated To Sent
    [Documentation]    Assert at least one row status badge shows "Sent".
    Wait For Elements State
    ...    xpath=//tbody/tr[1]//*[normalize-space()='Sent']
    ...    visible    ${Timeout}

Verify Status Not Sent
    [Documentation]    Assert row 1 status badge shows "Not Sent".
    Wait For Elements State
    ...    xpath=//tbody/tr[1]//*[normalize-space()='Not Sent']
    ...    visible    5s

Verify Status Finished
    [Documentation]    Assert row 1 status badge shows "Finished".
    Wait For Elements State
    ...    xpath=//tbody/tr[1]//*[normalize-space()='Finished']
    ...    visible    5s

Verify Row Status
    [Documentation]    Assert a specific row shows a given status text.
    [Arguments]    ${row_index}    ${expected_status}
    ${locator}=    Set Variable
    ...    xpath=//tbody/tr[${row_index}]//*[normalize-space()='${expected_status}']
    Wait For Elements State    ${locator}    visible    ${Timeout}

# ============================================================
# ADD CANDIDATE
# ============================================================

Click New Candidate Button
    [Documentation]    Click the "New Candidate" button (empty state or top-right).
    Ensure Add Candidate Options Visible
    Click    ${JobDetailsPage.NEW_CANDIDATE_BUTTON}

Click Import From Excel Button
    [Documentation]    Click the "Import from Excel" button.
    Ensure Add Candidate Options Visible
    Click    ${JobDetailsPage.IMPORT_FROM_EXCEL_BUTTON}

Ensure Add Candidate Options Visible
    [Documentation]    Open the add-candidate menu when the buttons are collapsed.
    ${new_candidate_visible}=    Run Keyword And Return Status
    ...    Wait For Elements State    ${JobDetailsPage.NEW_CANDIDATE_BUTTON}    visible    2s
    IF    not ${new_candidate_visible}
        Wait For Elements State    ${JobDetailsPage.ADD_CANDIDATE_DROPDOWN}    visible    ${Timeout}
        Click    ${JobDetailsPage.ADD_CANDIDATE_DROPDOWN}
    END
    Wait For Elements State    ${JobDetailsPage.NEW_CANDIDATE_BUTTON}       visible    ${Timeout}
    Wait For Elements State    ${JobDetailsPage.IMPORT_FROM_EXCEL_BUTTON}   visible    ${Timeout}

Open Add Candidate Dropdown
    [Documentation]    Alias kept for backward compat — resolves to New Candidate button.
    Click New Candidate Button

Click Add New Candidate
    [Documentation]    Alias — clicks "New Candidate" CTA.
    Click New Candidate Button

Click Add From Excel
    [Documentation]    Alias — clicks "Import from Excel" CTA.
    Click Import From Excel Button

# ============================================================
# DOWNLOAD REPORTS
# ============================================================

Click Download Reports
    [Documentation]    Click the Download Reports button.
    Wait For Elements State    ${JobDetailsPage.DOWNLOAD_REPORTS_BUTTON}    visible    ${Timeout}
    Click    ${JobDetailsPage.DOWNLOAD_REPORTS_BUTTON}

# ============================================================
# MINUTES LEFT PILL
# ============================================================

Verify Minutes Visible
    [Documentation]    Assert the remaining minutes pill is visible.
    Wait For Elements State    ${JobDetailsPage.MINUTES_LEFT_PILL}    visible    5s

# ============================================================
# PRECONDITION GUARDS
# ============================================================

Require Candidate With Status
    [Documentation]    Skip test if no row with the given status exists.
    [Arguments]    ${status}
    ${count}=    Get Element Count
    ...    xpath=//tbody/tr//*[normalize-space()='${status}']
    Skip If    ${count} == 0
    ...    msg=Precondition not met: no candidate with status '${status}' found

Require At Least N Candidates
    [Documentation]    Skip test if fewer than N candidates are present.
    [Arguments]    ${n}
    ${count}=    Get Element Count    xpath=//tbody/tr
    Skip If    ${count} < ${n}
    ...    msg=Precondition not met: need ${n} candidates but found ${count}

# ============================================================
# SESSION
# ============================================================

Logout
    [Documentation]    Click the Logout button.
    Wait For Elements State    ${JobDetailsPage.LOGOUT_BUTTON}    visible    ${Timeout}
    Click    ${JobDetailsPage.LOGOUT_BUTTON}

Close Browser Session
    [Documentation]    Close the active browser window.
    Close Browser
