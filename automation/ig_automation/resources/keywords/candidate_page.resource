*** Settings ***
Documentation    Candidates page action keywords — InterviewGod
...              All waits use explicit conditions; no Sleep() calls.
Library          Collections
Library          String
Resource         ../browser_compat.resource
Variables        ../../pages/candidates_page.py
Variables        ../../resources/variables/config.py


*** Keywords ***

# ──────────────────────────────────────────────────────────────────────────────
# NAVIGATION
# ──────────────────────────────────────────────────────────────────────────────

Navigate To Candidates Page
    [Documentation]    Navigate to /dashboard/candidates and wait for the heading
    ...                to confirm full load. Does NOT rely on the loading spinner.
    Go To                           ${BASE_URL}dashboard/candidates
    Wait Until Location Contains    /dashboard/candidates    timeout=15s
    Wait Until Page Contains        Candidates               timeout=15s
    Wait For Elements State         ${PAGE_HEADING}          visible    15s

Wait For Candidates List To Load
    [Documentation]    Waits until the loading text is gone AND the count label
    ...                or empty-state is present. Safe for 0-result pages.
    Run Keyword And Ignore Error
    ...    Wait For Elements State    ${LOADING_CANDIDATES_TEXT}    hidden    15s
    Wait For Elements State
    ...    xpath=//button[normalize-space()='View Details'] | //*[contains(normalize-space(),'Candidates') and string-length(normalize-space()) < 25 and contains(normalize-space(),' Candidates')]
    ...    visible    15s


Close Modal If Open
    [Documentation]    Dismisses any open modal overlay.
    ${status}    Run Keyword And Return Status
    ...    Wait For Elements State    ${ADD_MODAL_CLOSE_BTN}    visible    2s
    IF    ${status}
        Click    ${ADD_MODAL_CLOSE_BTN}
        Wait For Elements State    ${ADD_MODAL_CLOSE_BTN}    hidden    5s
    END


# ──────────────────────────────────────────────────────────────────────────────
# SEARCH
# ──────────────────────────────────────────────────────────────────────────────

Clear Search Input
    [Documentation]    Clears the search input robustly for React controlled inputs.
    Wait For Elements State    ${SEARCH_INPUT}    visible    5s

    # Standard clear via Ctrl+A + Delete (works for most React inputs)
    Click    ${SEARCH_INPUT}
    Keyboard Key    Press    Control+a
    Keyboard Key    Press    Delete

    # Check value — use JS fallback if React state blocked deletion
    ${value}=    Get Property    ${SEARCH_INPUT}    value
    IF    '${value}' != '${EMPTY}'
        Evaluate JavaScript
        ...    ${SEARCH_INPUT}
        ...    (element) => { element.value = ''; element.dispatchEvent(new Event('input', { bubbles: true })); }
    END

    # Post-condition verification
    ${final_value}=    Get Property    ${SEARCH_INPUT}    value
    Should Be Equal    ${final_value}    ${EMPTY}


Search For Candidate
    [Arguments]    ${search_term}
    [Documentation]    Clears + types into search and waits for list to update.
    ...                Pass empty string to restore full list.
    Clear Search Input
    IF    '${search_term}' != '${EMPTY}'
        Fill Text    ${SEARCH_INPUT}    ${search_term}
    END
    Wait For Candidates List To Load


Get Current Candidate Count As Integer
    [Documentation]    Returns the count of rendered candidate cards as integer.
    Wait For Candidates List To Load
    ${count}=    Get Element Count    ${VIEW_DETAILS_BTN}
    RETURN    ${count}

Get Candidate Card Count
    [Documentation]    Alias for Get Current Candidate Count As Integer.
    ${count}=    Get Current Candidate Count As Integer
    RETURN    ${count}


Get Candidate Count Label Text
    [Documentation]    Returns the text of the count label e.g. "1126 Candidates"
    Wait For Elements State
    ...    xpath=//*[matches(normalize-space(),'^\\d+ Candidates$')]    visible    10s
    ${text}=    Get Text
    ...    xpath=//*[matches(normalize-space(),'^\\d+ Candidates$')]
    RETURN    ${text}

# ══════════════════════════════════════════════════════════════
# ALL JOBS DROPDOWN
# ══════════════════════════════════════════════════════════════

Open All Jobs Dropdown
    [Documentation]    Clicks the All Jobs filter button and waits until the
    ...                dropdown list is present in the DOM.
    Wait For Elements State    ${ALL_JOBS_DROPDOWN_BTN}    visible    8s
    Click                      ${ALL_JOBS_DROPDOWN_BTN}
    Wait For Elements State
    ...    xpath=//*[@role='option' or @role='listbox' or @role='menu' or contains(@class,'dropdown-menu') or contains(@class,'options-list')]
    ...    visible    8s


Close All Jobs Dropdown Via Escape
    Keyboard Key    Press    Escape
    Wait For Elements State
    ...    xpath=//*[@role='listbox' or @role='menu' or contains(@class,'dropdown-menu')]
    ...    hidden    5s


Close All Jobs Dropdown Via Outside Click
    [Documentation]    Click the page heading to dismiss the dropdown.
    Click    ${PAGE_HEADING}
    Wait For Elements State
    ...    xpath=//*[@role='listbox' or @role='menu' or contains(@class,'dropdown-menu')]
    ...    hidden    5s


Select Job From Dropdown
    [Arguments]    ${job_name}
    [Documentation]    Opens the All Jobs dropdown and selects the option
    ...                whose text matches job_name exactly (normalize-space).
    Open All Jobs Dropdown
    ${job_option}=    Set Variable
    ...    xpath=//*[@role='option' and normalize-space()='${job_name}'] | //*[contains(@class,'option') and normalize-space()='${job_name}']
    Wait For Elements State    ${job_option}    visible    8s
    Click                      ${job_option}
    Wait For Candidates List To Load


Select All Jobs Option
    [Documentation]    Resets the job filter back to "All Jobs".
    ${current_btn_text}=    Get Text    ${ALL_JOBS_DROPDOWN_BTN}
    IF    'All Jobs' not in '${current_btn_text}'
        Open All Jobs Dropdown
        Wait For Elements State    ${ALL_JOBS_OPTION}    visible    8s
        Click                      ${ALL_JOBS_OPTION}
        Wait For Candidates List To Load
    END


# ══════════════════════════════════════════════════════════════
# SORT PANEL
# ══════════════════════════════════════════════════════════════

Open Sort Panel
    [Documentation]    Opens the Sort by panel and verifies it is visible.
    Wait For Elements State    ${SORT_BY_BTN}          visible    8s
    Click                      ${SORT_BY_BTN}
    Wait For Elements State    ${SORT_NAME_ASCENDING}  visible    8s


Close Sort Panel
    Keyboard Key    Press    Escape
    Run Keyword And Ignore Error
    ...    Wait For Elements State    ${SORT_NAME_ASCENDING}    hidden    5s


Apply Sort
    [Arguments]    ${sort_locator}
    [Documentation]    Opens Sort panel, clicks the given option label, applies.
    Open Sort Panel
    Wait For Elements State    ${sort_locator}    visible    5s
    Click                      ${sort_locator}
    Wait For Elements State    ${SORT_APPLY_BTN}    visible    5s
    Click                      ${SORT_APPLY_BTN}
    Wait For Candidates List To Load


Clear Sort
    Open Sort Panel
    Wait For Elements State    ${SORT_CLEAR_BTN}    visible    5s
    Click                      ${SORT_CLEAR_BTN}
    Wait For Candidates List To Load


# ══════════════════════════════════════════════════════════════
# FILTER PANEL
# ══════════════════════════════════════════════════════════════

Open Filter Panel
    Wait For Elements State    ${FILTER_BY_BTN}          visible    8s
    Click                      ${FILTER_BY_BTN}
    Wait For Elements State    ${FILTER_STATUS_SENT}    visible    8s


Close Filter Panel
    Keyboard Key    Press    Escape
    Run Keyword And Ignore Error
    ...    Wait For Elements State    ${FILTER_STATUS_SENT}    hidden    5s


Apply Status Filter
    [Arguments]    ${status_checkbox_locator}
    [Documentation]    Opens filter panel, checks a status checkbox, and applies.
    Open Filter Panel
    Wait For Elements State    ${status_checkbox_locator}    visible    5s
    Check Checkbox             ${status_checkbox_locator}
    Wait For Elements State    ${FILTER_APPLY_BTN}           visible    5s
    Click                      ${FILTER_APPLY_BTN}
    Wait For Candidates List To Load


Clear All Filters
    Open Filter Panel
    Wait For Elements State    ${FILTER_CLEAR_BTN}    visible    5s
    Click                      ${FILTER_CLEAR_BTN}
    Wait For Candidates List To Load


# ══════════════════════════════════════════════════════════════
# MODALS — ADD CANDIDATE
# ══════════════════════════════════════════════════════════════

Open Add Candidate Modal
    Wait For Elements State    ${ADD_CANDIDATES_BTN}    visible    8s
    Click                      ${ADD_CANDIDATES_BTN}
    Wait For Elements State    ${ADD_MODAL_TITLE}       visible    8s


Close Add Candidate Modal
    Wait For Elements State    ${ADD_MODAL_CLOSE_BTN}    visible    5s
    Click                      ${ADD_MODAL_CLOSE_BTN}
    Wait For Elements State    ${ADD_MODAL_TITLE}        hidden     8s


Fill Add Candidate Form
    [Arguments]    ${name}    ${email}    ${phone}
    Wait For Elements State    ${ADD_MODAL_NAME}    visible    5s
    Fill Text    ${ADD_MODAL_NAME}     ${name}
    Fill Text    ${ADD_MODAL_EMAIL}    ${email}
    Fill Text    ${ADD_MODAL_PHONE}    ${phone}


# ══════════════════════════════════════════════════════════════
# MODALS — IMPORT EXCEL
# ══════════════════════════════════════════════════════════════

Open Import Excel Modal
    Wait For Elements State    ${IMPORT_FROM_EXCEL_BTN}       visible    8s
    Click                      ${IMPORT_FROM_EXCEL_BTN}
    Wait For Elements State    ${IMPORT_EXCEL_MODAL_TITLE}    visible    8s


Close Import Excel Modal
    Wait For Elements State    ${IMPORT_EXCEL_CANCEL_BTN}     visible    5s
    Click                      ${IMPORT_EXCEL_CANCEL_BTN}
    Wait For Elements State    ${IMPORT_EXCEL_MODAL_TITLE}    hidden     8s


# ══════════════════════════════════════════════════════════════
# VIEW DETAILS MODAL
# ══════════════════════════════════════════════════════════════

Open View Details Modal For First Candidate
    Wait For Elements State    ${VIEW_DETAILS_FIRST}           visible    10s
    Click                      ${VIEW_DETAILS_FIRST}
    Wait For Elements State    ${VIEW_DETAILS_MODAL_TITLE}    visible     8s


Close View Details Modal
    Wait For Elements State    ${VIEW_DETAILS_CLOSE_BTN}      visible    5s
    Click                      ${VIEW_DETAILS_CLOSE_BTN}
    Wait For Elements State    ${VIEW_DETAILS_MODAL_TITLE}    hidden     8s


# ══════════════════════════════════════════════════════════════
# THREE-DOT CONTEXT MENU
# ══════════════════════════════════════════════════════════════

Open Three Dot Menu For First Candidate
    Wait For Elements State    ${THREE_DOT_MENU_FIRST}    visible    10s
    Click                      ${THREE_DOT_MENU_FIRST}
    Wait For Elements State    ${MENU_EDIT_DETAILS}        visible     5s


Close Three Dot Menu Via Escape
    Keyboard Key    Press    Escape
    Run Keyword And Ignore Error
    ...    Wait For Elements State    ${MENU_EDIT_DETAILS}    hidden    5s
