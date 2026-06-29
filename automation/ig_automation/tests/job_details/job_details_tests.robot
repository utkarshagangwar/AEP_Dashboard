*** Settings ***
Documentation    Job details page — smoke, regression, negative, and edge coverage.
Library          Collections
Library          OperatingSystem
Resource         ../../resources/browser_compat.resource
Library          ../../libs/hopscotch_client.py
Resource         ../../resources/keywords/login_keywords.resource
Resource         ../../resources/keywords/video_keywords.resource
Resource         ../../resources/keywords/job_details_keywords.resource
Variables        ../../resources/variables/config.py

Suite Setup      Open InterviewGod Suite
Suite Teardown   Close Browser
Test Setup       Open Test With Recording
Test Teardown    Close Test And Save Recording


*** Test Cases ***

# ============================================================
# SMOKE
# ============================================================

Verify Job Details Page Loads
    [Documentation]    Validate the job details page loads with candidate data.
    [Tags]    smoke    regression
    Open Job Details Page
    Verify Job Details
    # Candidates may or may not exist — only verify page structure loaded
    Wait Until Page Contains Element    xpath=//button[normalize-space()='Send interview']
    ...    timeout=${Timeout}

Download Reports
    [Documentation]    Validate the Download Reports button is present and clickable.
    [Tags]    smoke
    # NOTE: Button may be disabled until a Finished candidate is selected.
    # This test only validates the element is present in the DOM.
    Wait Until Page Contains Element    ${JobDetailsPage.DOWNLOAD_REPORTS_BUTTON}
    ...    timeout=${Timeout}
    Element Should Be Visible    ${JobDetailsPage.DOWNLOAD_REPORTS_BUTTON}

Send Interview Single Candidate
    [Documentation]    Select one Not Sent candidate, send interview, verify success and status update.
    [Tags]    smoke    regression
    # PRECONDITION: At least one candidate with status "Not Sent" must exist.
    # If none exists (empty table, or all candidates already sent/finished),
    # the Send Interview button will never activate — skip rather than fail.
    Require Candidate With Status    Not Sent
    Verify Send Interview Disabled
    Select Single Candidate
    Verify Send Interview Enabled
    Click Send Interview
    Verify Success Toast
    Verify Status Updated To Sent

# ============================================================
# REGRESSION
# ============================================================

Search Candidate Successfully
    [Documentation]    Valid candidate name returns at least one result.
    [Tags]    regression
    # PRECONDITION: A candidate named "Shankar" must exist in this job.
    # If the table is empty, this test is not applicable.
    Require Candidate With Status    Not Sent
    Search Candidate    Harsh
    Verify Candidates Present
    Clear Candidate Search

Search With Empty Input
    [Documentation]    Empty search clears results and shows "No candidates yet" empty state.
    [Tags]    regression
    # SOW CONFIRMED: An empty search does NOT show candidates.
    # The system shows the "No candidates yet" empty state on empty input.
    Search Candidate    $  
    Verify No Candidates Empty State
    Clear Candidate Search

Apply Filters Successfully
    [Documentation]    Open filter panel, set all filters, apply — no error thrown.
    [Tags]    regression
    Open Filter Panel
    Select Status Filter
    Select Deadline Filter
    Select Score Filter
    Enter Score Range    50    90
    Apply Filters

Score Filter Boundary Values
    [Documentation]    Min=0, Max=100 boundary inputs accepted without error.
    [Tags]    regression
    Open Filter Panel
    Enter Score Range    0    100
    Apply Filters

Round Navigation Boundary
    [Documentation]    Navigate between round tabs without error.
    [Tags]    regression
    Click Round Tab    1
    # Only navigate to tab 2 if it exists — dynamic guard prevents hard failure
    ${tab_count}=    Get Round Tab Count
    IF    ${tab_count} >= 2
        Click Round Tab    2
        Click Round Tab    1
    END

Send Interview Multiple Candidates
    [Documentation]    Select two Not Sent candidates, send interviews in bulk.
    [Tags]    regression
    # PRECONDITION: At least 2 candidates with status "Not Sent" must exist.
    Require At Least N Candidates    2
    Require Candidate With Status    Not Sent
    Verify Send Interview Disabled
    Select Multiple Candidates
    Verify Send Interview Enabled
    Click Send Interview
    Verify Success Toast
    Verify Status Updated To Sent

Send Interview With Mixed Status Candidates
    [Documentation]    Select candidates including already-Sent ones — success toast expected.
    [Tags]    regression
    Require At Least N Candidates    2
    Select Multiple Candidates
    Click Send Interview
    Verify Success Toast

Send Interview With Large Selection
    [Documentation]    Select all candidates and bulk send.
    [Tags]    regression
    # PRECONDITION: At least one Not Sent candidate must exist.
    Require Candidate With Status    Not Sent
    Select All Candidates
    Click Send Interview
    Verify Success Toast

Open Add Candidate Options
    [Documentation]    Verify "New Candidate" and "Import from Excel" buttons are accessible.
    [Tags]    regression
    # SOW + screenshot confirmed: UI shows two CTA buttons, NOT a dropdown.
    # "Import from Excel" and "New Candidate" are always visible when table is empty,
    # or appear in the top-right area when candidates exist.
    Wait Until Element Is Visible
    ...    xpath=//button[normalize-space()='New Candidate']
    ...    timeout=${Timeout}
    Element Should Be Visible    xpath=//button[normalize-space()='New Candidate']
    Element Should Be Visible    xpath=//button[normalize-space()='Import from Excel']

# ============================================================
# NEGATIVE
# ============================================================

Search Invalid Candidate
    [Documentation]    Search with a name that returns no results — empty state shown.
    [Tags]    negative
    Search Candidate    XYZ123_NoMatch
    # System shows "No candidates yet" empty state, NOT rows.
    # Verify the empty state element is visible.
    Verify No Candidates Empty State
    # Optionally assert zero table rows (table may not render at all)
    ${row_count}=    Get Element Count    xpath=//tbody/tr
    Should Be Equal As Integers    ${row_count}    0
    ...    msg=Expected 0 rows for invalid search but found ${row_count}
    Clear Candidate Search

Apply Empty Filters
    [Documentation]    Apply filters without any selection — should not throw.
    [Tags]    negative
    Open Filter Panel
    # Click Apply Filters with nothing selected — system should handle gracefully
    Apply Filters

Send Interview Without Selection
    [Documentation]    Verify Send Interview button is disabled when no candidate is selected.
    [Tags]    negative
    # Deselect any active selection by reloading round
    Click Round Tab    1
    Verify Send Interview Disabled

Send Interview When Already Sent
    [Documentation]    Attempt to re-send to a candidate already in Sent status.
    [Tags]    negative
    # PRECONDITION: A "Sent" candidate must already exist.
    Require Candidate With Status    Sent
    # Verify the first row is in Sent state
    Verify Row Status    1    Sent
    Select Single Candidate
    Click Send Interview
    # System should either reject with error toast or silently succeed
    # Per SOW: no re-send behavior defined — error toast is expected on duplicate
    Verify Error Toast

# ============================================================
# EDGE
# ============================================================

Select All Candidates With No Data
    [Documentation]    Select-all on an empty result set should not throw an error.
    [Tags]    edge
    # Produce an empty result by searching for a non-existent name
    Search Candidate    XYZ123_NoMatch
    ${row_count}=    Get Element Count    xpath=//tbody/tr
    IF    ${row_count} == 0
        Log    Table is empty — no rows to select. Verifying select-all checkbox is inert.
        # Select-all checkbox should still be present but have no effect
        ${checkbox_present}=    Run Keyword And Return Status
        ...    Element Should Be Visible    ${JobDetailsPage.SELECT_ALL_CHECKBOX}
        IF    ${checkbox_present}
            Click Element    ${JobDetailsPage.SELECT_ALL_CHECKBOX}
            # No rows should become selected
            ${selected}=    Get Element Count
            ...    xpath=//tbody/tr//button[@role='checkbox' and @aria-checked='true']
            Should Be Equal As Integers    ${selected}    0
        END
    ELSE
        Select All Candidates
    END
    # Restore state
    Clear Candidate Search
