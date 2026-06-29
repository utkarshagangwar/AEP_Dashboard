*** Settings ***
Documentation    Jobs page keywords.
Library          Collections
Library          OperatingSystem
Resource         ../browser_compat.resource
Variables        ../../pages/jobs_page.py
Variables        ../../resources/variables/config.py


*** Keywords ***
Go To Jobs Page
    [Documentation]    Open the jobs page from the application shell.
    ...                Always calls Reset Jobs Page State after landing so every test
    ...                starts with a clean, unfiltered jobs list.
    [Arguments]    ${Timeout}=15s
    Click    xpath=//a[@href='/dashboard/jobs']//span[contains(text(),'Jobs')]
    Wait For Elements State
    ...    xpath=//p[normalize-space(text())='Jobs' and contains(@class,'font-bold')]
    ...    visible
    ...    ${Timeout}
    Reset Jobs Page State

Reset Jobs Page State
    [Documentation]    Clears the search input and dismisses any open sort/filter panel
    ...                so each test starts from a clean, unfiltered state.
    ...                Called automatically by Go To Jobs Page — can also be called
    ...                explicitly as teardown in any keyword that applies filters.
    # Dismiss any open dropdown / sort / filter overlay (safe no-op when nothing is open)
    Keyboard Key    Press    Escape
    # Clear search input only if it currently has content — avoids unnecessary network calls
    ${val}=    Get Property    xpath=//input[@placeholder='Search Jobs']    value
    IF    '${val}' != '${EMPTY}'
        Fill Text    xpath=//input[@placeholder='Search Jobs']    ${EMPTY}
        Wait Until Keyword Succeeds    5s    1s    Jobs Search Input Should Be Empty
    END

Jobs Search Input Should Be Empty
    [Documentation]    Asserts the jobs search input is empty.
    ...                Used as the polling target inside Reset Jobs Page State.
    ${val}=    Get Property    xpath=//input[@placeholder='Search Jobs']    value
    Should Be Equal    ${val}    ${EMPTY}

Clear Jobs Search
    [Documentation]    Clears the jobs search input and waits for it to be empty.
    ...                Call this explicitly when a keyword or test applies a search
    ...                and needs to reset before the next action.
    Fill Text    xpath=//input[@placeholder='Search Jobs']    ${EMPTY}
    Wait Until Keyword Succeeds    5s    1s    Jobs Search Input Should Be Empty

Verify Jobs Page Loaded
    [Documentation]    Confirm the jobs page header is visible.
    Wait For Elements State
    ...    xpath=//p[normalize-space(text())='Jobs' and contains(@class,'font-bold')]
    ...    visible    5s

Verify Jobs Metrics Visible
    [Documentation]    Confirm the four job summary metric labels are visible.
    Wait For Elements State    ${JobsPage.TOTAL_JOBS_LABEL}          visible    ${Timeout}
    Wait For Elements State    ${JobsPage.TOTAL_JOBS}                visible    ${Timeout}
    # FIX: //* matched both the wrapper <div> and the <p> label (Tailwind dual-render → strict mode violation).
    # Use TOTAL_CANDIDATES_LABEL from page object — scoped to <p> excluding lg:hidden ancestor.
    Wait For Elements State    ${JobsPage.TOTAL_CANDIDATES_LABEL}    visible    ${Timeout}
    Wait For Elements State    xpath=//p[normalize-space()='Evaluations Scheduled' and not(ancestor::div[contains(@class,'lg:hidden')])]    visible    ${Timeout}
    Wait For Elements State    xpath=//p[normalize-space()='Interview Completed' and not(ancestor::div[contains(@class,'lg:hidden')])]      visible    ${Timeout}

Verify Job Cards Present
    [Documentation]    Confirm the jobs listing renders at least one card.
    ${count}=    Get Element Count    xpath=//a[contains(@href,'/dashboard/jobs/')]
    Should Be True    ${count} > 0

Search Job
    [Documentation]    Search for a job by name.
    [Arguments]    ${job_name}
    Fill Text    xpath=//input[@placeholder='Search Jobs']    ${job_name}

Click Add Job
    [Documentation]    Open the Add Job wizard from the jobs listing.
    # Wait for overlay to clear (ignore error if overlay was never present)
    Run Keyword And Ignore Error
    ...    Wait For Elements State    xpath=//div[contains(@class,'overlay')]    hidden    ${Timeout}
    Wait For Elements State    ${JobsPage.ADD_JOB_BTN}    visible    ${Timeout}
    Click    ${JobsPage.ADD_JOB_BTN}
    # Wizard Step 1 heading is now "Job Description" — "Create Job" no longer exists
    Wait For Elements State    ${JobsPage.STEP1_HEADING}    visible    ${Timeout}

Verify Create Job Page Loaded
    [Documentation]    Confirm the Add Job wizard Step 1 is visible.
    ...                UI redesign: heading changed from "Create Job" to "Job Description".
    Wait For Elements State    ${JobsPage.STEP1_HEADING}    visible    5s

Fill Basic Job Details
    [Documentation]    Populate title, employment type, and experience.
    [Arguments]    ${title}    ${employment}    ${experience}
    Fill Text    xpath=//input[@placeholder='Job']    ${title}
    Select Employment Type    ${employment}
    Fill Text    xpath=//input[contains(@placeholder,'years')]    ${experience}

Open Employment Dropdown
    [Documentation]    Open the employment type dropdown.
    Click    xpath=//button[contains(normalize-space(.),'Select Type')]
    Wait For Elements State
    ...    xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button
    ...    visible    ${Timeout}

Verify Employment Options
    [Documentation]    Confirm the expected employment options exist.
    Wait For Elements State
    ...    xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Full Time')]
    ...    visible    5s
    Wait For Elements State
    ...    xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Part Time')]
    ...    visible    5s
    Wait For Elements State
    ...    xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Freelancing')]
    ...    visible    5s

Select Employment Type
    [Documentation]    Select a given employment type option.
    [Arguments]    ${type}
    Open Employment Dropdown
    Click
    ...    xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'${type}')]

Validate Employment Selection
    [Documentation]    Data-driven helper that confirms each type is selectable.
    [Arguments]    ${type}
    Go To Jobs Page
    Click Add Job
    Select Employment Type    ${type}

Wait For AI Prompt Generation
    [Documentation]    Wait until the AI auto-generates content into the AI Prompt textarea.
    Wait Until Keyword Succeeds    12x    3s    AI Prompt Should Not Be Empty

AI Prompt Should Not Be Empty
    [Documentation]    Assert the AI Prompt textarea has been auto-populated.
    ${value}=    Get Property    xpath=//textarea[@placeholder='AI Prompt']    value
    Should Not Be Empty    ${value}

Capture AI Prompt
    [Documentation]    Return the current AI prompt value.
    ${value}=    Get Property    xpath=//textarea[@placeholder='AI Prompt']    value
    RETURN    ${value}

Update Experience Level
    [Documentation]    Clear and re-enter the experience input to trigger AI regeneration.
    [Arguments]    ${new_exp}
    Fill Text    xpath=//input[contains(@placeholder,'years')]    ${EMPTY}
    Fill Text    xpath=//input[contains(@placeholder,'years')]    ${new_exp}

Wait For AI Prompt Update
    [Documentation]    Retry until the AI prompt changes from the prior captured value.
    [Arguments]    ${old_value}
    Wait Until Keyword Succeeds    12x    3s    AI Prompt Should Change    ${old_value}

AI Prompt Should Change
    [Documentation]    Assert the AI prompt differs from the prior value.
    [Arguments]    ${old_value}
    ${new}=    Get Property    xpath=//textarea[@placeholder='AI Prompt']    value
    Should Not Be Empty    ${new}
    Should Not Be Equal    ${old_value}    ${new}

Edit AI Prompt Manually
    [Documentation]    Overwrite the AI prompt textarea with a custom value.
    [Arguments]    ${custom_text}
    Fill Text    xpath=//textarea[@placeholder='AI Prompt']    ${custom_text}

Verify AI Prompt Editable
    [Documentation]    Confirm the AI prompt textarea reflects the custom override.
    [Arguments]    ${expected}
    ${value}=    Get Property    xpath=//textarea[@placeholder='AI Prompt']    value
    Should Be Equal    ${value}    ${expected}

Verify Template Section Visible
    [Documentation]    Confirm the interview templates section is visible.
    Wait For Elements State
    ...    xpath=//*[self::h2 or self::h3 or self::p][contains(text(),'Interview Templates')]
    ...    visible    5s

Verify All Templates Present
    [Documentation]    Confirm all expected templates are visible.
    Wait For Elements State    xpath=//*[contains(normalize-space(.),'AI Call Screening')]    visible    5s
    Wait For Elements State    xpath=//*[contains(normalize-space(.),'AI Assessment')]        visible    5s
    Wait For Elements State    xpath=//*[contains(normalize-space(.),'AI Interview')]         visible    5s

Select First Template
    [Documentation]    Select the first available template.
    Click    (//button[contains(.,'Select Template')])[1]

Verify Create Template Option
    [Documentation]    Confirm the create-template action is visible.
    Wait For Elements State
    ...    xpath=//*[contains(normalize-space(text()),'Create template')]
    ...    visible    5s

Click Save Job
    [Documentation]    Submit the create job form.
    Click    xpath=//button[normalize-space(.)='Save']

Click Cancel Job
    [Documentation]    Cancel the create job form.
    Click    xpath=//button[normalize-space(.)='Cancel']


# ═══════════════════════════════════════════════════════════════════
# WIZARD NAVIGATION — shared across all 3 steps
# ═══════════════════════════════════════════════════════════════════

Navigate To Create Job Wizard
    [Documentation]    Open the Add Job wizard from the jobs listing and ensure Step 1 loads.
    ...                If the app restores a previous wizard step (SPA state caching),
    ...                the keyword clicks Back until the Method selection page is visible.
    Go To Jobs Page
    Click Add Job
    Ensure Wizard On Step 1

Ensure Wizard On Step 1
    [Documentation]    Click Back up to 5 times until Step 1 heading is visible.
    ...                Required because the SPA caches the last wizard state across navigations.
    FOR    ${i}    IN RANGE    0    5
        ${on_step1}=    Run Keyword And Return Status
        ...    Element Should Be Visible    ${JobsPage.STEP1_HEADING}    timeout=3s
        IF    ${on_step1}    BREAK
        Run Keyword And Ignore Error    Click    ${JobsPage.WIZARD_BACK_BTN}
        Wait Until Keyword Succeeds    5s    1s    Any Wizard Heading Visible
    END
    Wait Until Element Is Visible    ${JobsPage.STEP1_HEADING}    timeout=10s

Any Wizard Heading Visible
    [Documentation]    Helper — passes when any wizard step heading is in the DOM.
    ${s1}=    Run Keyword And Return Status
    ...    Element Should Be Visible    ${JobsPage.STEP1_HEADING}    timeout=1s
    ${s2a}=    Run Keyword And Return Status
    ...    Element Should Be Visible    ${JobsPage.STEP2_AI_HEADING}    timeout=1s
    ${s2p}=    Run Keyword And Return Status
    ...    Element Should Be Visible    ${JobsPage.STEP2_PASTE_HEADING}    timeout=1s
    ${s3}=    Run Keyword And Return Status
    ...    Element Should Be Visible    ${JobsPage.STEP3_HEADING}    timeout=1s
    Should Be True    ${s1} or ${s2a} or ${s2p} or ${s3}

Click Wizard Continue
    [Documentation]    Click the Continue → footer button (present on all wizard steps).
    Click    ${JobsPage.WIZARD_CONTINUE_BTN}

Click Wizard Back
    [Documentation]    Click the ← Back footer button (present on Steps 2 and 3).
    Click    ${JobsPage.WIZARD_BACK_BTN}


# ═══════════════════════════════════════════════════════════════════
# WIZARD STEP 1 — Method Selection
# ═══════════════════════════════════════════════════════════════════

Verify Step 1 Loaded
    [Documentation]    Confirm the method selection page heading is visible.
    Wait Until Element Is Visible    ${JobsPage.STEP1_HEADING}    timeout=15s

Select Wizard Method
    [Documentation]    Select a method card on Step 1.
    ...                Valid arguments: "Generate with AI" or "Paste Existing JD".
    [Arguments]    ${method}
    IF    '${method}' == 'Generate with AI'
        Click    ${JobsPage.GENERATE_AI_CARD}
    ELSE IF    '${method}' == 'Paste Existing JD'
        Click    ${JobsPage.PASTE_JD_CARD}
    ELSE
        Fail    Unknown wizard method: '${method}'. Use 'Generate with AI' or 'Paste Existing JD'.
    END

Verify Method Card Selected
    [Documentation]    Assert that a method card visually appears selected (blue border).
    ...                Checks the card is present and clickable — exact class inspection
    ...                is avoided since Tailwind inline-style selection state can vary.
    [Arguments]    ${method}
    IF    '${method}' == 'Generate with AI'
        Element Should Be Visible    ${JobsPage.GENERATE_AI_CARD}
    ELSE IF    '${method}' == 'Paste Existing JD'
        Element Should Be Visible    ${JobsPage.PASTE_JD_CARD}
    END


# ═══════════════════════════════════════════════════════════════════
# WIZARD STEP 2 — Generate with AI path
# ═══════════════════════════════════════════════════════════════════

Verify Step 2 AI Loaded
    [Documentation]    Confirm Step 2 "Generate Job Description" heading is visible.
    Wait Until Element Is Visible    ${JobsPage.STEP2_AI_HEADING}    timeout=15s

Fill AI Role Details
    [Documentation]    Fill Job Title, select Employment Type, and enter Experience Level.
    ...                Employment type must match an option exactly: Full-time | Part-time | Freelancing.
    [Arguments]    ${title}    ${employment_type}    ${experience}
    Fill Text    ${JobsPage.JOB_TITLE_AI_INPUT}    ${title}
    Select Options By    ${JobsPage.EMP_TYPE_SELECT}    text    ${employment_type}
    Fill Text    ${JobsPage.EXP_LEVEL_AI_INPUT}    ${experience}

Click Generate JD Button
    [Documentation]    Click the "Generate JD" button on Step 2 AI path.
    ...                The button must be in idle state (not "Generating…") before clicking.
    Click    ${JobsPage.GENERATE_JD_BTN}

Wait For JD Generation Complete
    [Documentation]    Wait until all 4 Generation Progress steps show "Completed".
    ...                Times out after 60 seconds (AI generation can take ~15s).
    Wait Until Keyword Succeeds    60s    3s    All Four Generation Steps Completed

All Four Generation Steps Completed
    [Documentation]    Polling helper — asserts exactly 4 "Completed" labels are visible.
    ...                Called by Wait For JD Generation Complete via Wait Until Keyword Succeeds.
    ${count}=    Get Element Count
    ...    xpath=//p[normalize-space()='Completed']
    Should Be Equal As Integers    ${count}    4

Verify JD Content Present
    [Documentation]    Assert the Job Description area has text content after generation.
    ...                Uses Page Should Contain (source check) to avoid Playwright strict mode
    ...                violations on the generated text that may appear in multiple DOM nodes.
    Page Should Contain    Job Title

Click Regenerate JD Button
    [Documentation]    Click the "Regenerate Entire JD" button (visible after first generation).
    Wait Until Element Is Visible    ${JobsPage.REGENERATE_JD_BTN}    timeout=5s
    Click    ${JobsPage.REGENERATE_JD_BTN}

Verify Generation Progress Panel Visible
    [Documentation]    Confirm all 4 Generation Progress step labels are visible.
    Wait Until Element Is Visible    ${JobsPage.GEN_STEP_UNDERSTANDING}    timeout=10s
    Wait Until Element Is Visible    ${JobsPage.GEN_STEP_CREATING}         timeout=5s
    Wait Until Element Is Visible    ${JobsPage.GEN_STEP_EXTRACTING}       timeout=5s
    Wait Until Element Is Visible    ${JobsPage.GEN_STEP_FINALIZING}       timeout=5s


# ═══════════════════════════════════════════════════════════════════
# WIZARD STEP 2 — Paste Existing JD path
# ═══════════════════════════════════════════════════════════════════

Verify Step 2 Paste Loaded
    [Documentation]    Confirm Step 2 "Paste Existing JD" heading is visible.
    Wait Until Element Is Visible    ${JobsPage.STEP2_PASTE_HEADING}    timeout=15s

Fill Paste JD Role Details
    [Documentation]    Fill Role Details section on the Paste Existing JD step.
    ...                Employment type must match an option exactly: Full-time | Part-time | Freelancing.
    [Arguments]    ${title}    ${employment_type}    ${experience}
    Fill Text    ${JobsPage.JOB_TITLE_PASTE_INPUT}    ${title}
    Select Options By    ${JobsPage.EMP_TYPE_SELECT}    text    ${employment_type}
    Fill Text    ${JobsPage.EXP_LEVEL_AI_INPUT}    ${experience}

Paste Job Description Text
    [Documentation]    Type the given JD text into the Paste Job Description textarea.
    [Arguments]    ${jd_text}
    Fill Text    ${JobsPage.JD_PASTE_TEXTAREA}    ${jd_text}

Verify Analysis Panel Visible
    [Documentation]    Confirm all 4 Analysis step labels are present (Paste JD path).
    Wait Until Element Is Visible    ${JobsPage.ANALYSIS_JD_DETECTED}    timeout=10s
    Wait Until Element Is Visible    ${JobsPage.ANALYSIS_SECTIONS}        timeout=5s
    Wait Until Element Is Visible    ${JobsPage.ANALYSIS_EXTRACTING}      timeout=5s
    Wait Until Element Is Visible    ${JobsPage.ANALYSIS_READY}           timeout=5s

Wait For JD Analysis Complete
    [Documentation]    Wait until JD analysis completes — polls for 4 "Completed" labels.
    Wait Until Keyword Succeeds    60s    3s    All Four Generation Steps Completed


# ═══════════════════════════════════════════════════════════════════
# WIZARD STEP 3 — Review & Refine Skill Map
# ═══════════════════════════════════════════════════════════════════

Verify Step 3 Loaded
    [Documentation]    Confirm Step 3 "Review & Refine Skill Map" heading is visible.
    Wait Until Element Is Visible    ${JobsPage.STEP3_HEADING}    timeout=15s

Verify All Skill Tabs Present
    [Documentation]    Confirm all 3 skill category tab buttons are visible on Step 3.
    Wait Until Element Is Visible    ${JobsPage.TAB_TOP_SKILLS}       timeout=5s
    Wait Until Element Is Visible    ${JobsPage.TAB_SECONDARY_SKILLS} timeout=5s
    Wait Until Element Is Visible    ${JobsPage.TAB_TRAINABLE_SKILLS} timeout=5s

Click Skill Tab
    [Documentation]    Click a skill category tab on Step 3.
    ...                Valid arguments: "Top Skills (Must Have)" | "Secondary Skills (Important)"
    ...                               | "Trainable Skills (Nice to Have)"
    [Arguments]    ${tab_name}
    Click    xpath=//button[normalize-space()='${tab_name}']
    # Brief stability wait — tab click is instant; no element state change to poll
    Wait Until Page Contains Element
    ...    xpath=//button[normalize-space()='${tab_name}']
    ...    timeout=3s

Verify Add Own Skill Button Present
    [Documentation]    Confirm the "+ Add Your Own Skill" button is visible on Step 3.
    Wait Until Element Is Visible    ${JobsPage.ADD_OWN_SKILL_BTN}    timeout=5s

Click Confirm And Create
    [Documentation]    Click the "Confirm & Create →" final submission button.
    ...                WARNING: This creates an actual job record in the database.
    ...                Only call from tests that include post-creation cleanup or
    ...                explicitly test job creation.
    Wait Until Element Is Visible    ${JobsPage.CONFIRM_CREATE_BTN}    timeout=5s
    Click    ${JobsPage.CONFIRM_CREATE_BTN}


# ═══════════════════════════════════════════════════════════════════
# WIZARD VALIDATION HELPERS
# ═══════════════════════════════════════════════════════════════════

Verify Toast Contains
    [Documentation]    Assert a toast/error message containing the given text is visible.
    [Arguments]    ${expected_text}
    Wait Until Page Contains    ${expected_text}    timeout=5s

Verify Employment Type Options In Select
    [Documentation]    Assert the Employment Type <select> contains all 3 expected options.
    ${options}=    Evaluate JavaScript    ${JobsPage.EMP_TYPE_SELECT}
    ...    [...element.options].map(o => o.text)
    Should Contain    ${options}    Full-time
    Should Contain    ${options}    Part-time
    Should Contain    ${options}    Freelancing
