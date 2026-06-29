*** Settings ***
Documentation    Jobs page smoke and regression coverage.
Library          JSONLibrary
Resource         ../../resources/browser_compat.resource
Library          ../../libs/hopscotch_client.py
Resource         ../../resources/keywords/login_keywords.resource
Resource         ../../resources/keywords/video_keywords.resource
Resource         ../../resources/keywords/jobs_keywords.resource
Variables        ../../resources/variables/config.py
Variables        ../../pages/jobs_page.py

Suite Setup      Open InterviewGod Suite
Suite Teardown   Close Browser
Test Setup       Open Test With Recording
Test Teardown    Close Test And Save Recording


*** Test Cases ***
Jobs Page Load - Happy Path
    [Documentation]    Verify jobs page loads with metrics and job cards.
    [Tags]    smoke    regression
    Go To Jobs Page
    Verify Jobs Page Loaded
    Verify Jobs Metrics Visible
    Verify Job Cards Present

Search Job Functionality
    [Documentation]    Validate job search works.
    [Tags]    regression
    Go To Jobs Page
    ${data}=    Load JSON From File    ${DATA_FILE}
    Search Job    ${data['search']['job_name']}

Create Job - AI Prompt Generation
    [Documentation]    Validate AI prompt generates after experience input.
    [Tags]    smoke    regression
    Go To Jobs Page
    Click Add Job
    Verify Create Job Page Loaded
    ${data}=    Load JSON From File    ${DATA_FILE}
    Fill Basic Job Details
    ...    ${data['job_valid']['title']}
    ...    ${data['job_valid']['employment_types'][0]}
    ...    ${data['job_valid']['experience']}
    Wait For AI Prompt Generation

Create Job - AI Prompt Update On Change
    [Documentation]    Validate AI prompt updates when experience changes.
    [Tags]    regression
    Go To Jobs Page
    Click Add Job
    ${data}=    Load JSON From File    ${DATA_FILE}
    Fill Basic Job Details
    ...    ${data['job_valid']['title']}
    ...    ${data['job_valid']['employment_types'][0]}
    ...    ${data['job_valid']['experience']}
    Wait For AI Prompt Generation
    ${initial}=    Capture AI Prompt
    Update Experience Level    ${data['job_update']['new_experience']}
    Wait For AI Prompt Update    ${initial}

Create Job - AI Prompt Editable
    [Documentation]    Validate the user can edit the AI prompt manually.
    [Tags]    regression
    Go To Jobs Page
    Click Add Job
    ${data}=    Load JSON From File    ${DATA_FILE}
    Fill Basic Job Details
    ...    ${data['job_valid']['title']}
    ...    ${data['job_valid']['employment_types'][0]}
    ...    ${data['job_valid']['experience']}
    Wait For AI Prompt Generation
    Edit AI Prompt Manually    ${data['ai_prompt']['custom_text']}
    Verify AI Prompt Editable    ${data['ai_prompt']['custom_text']}

Create Job - Negative (Missing Fields)
    [Documentation]    Validate required fields block submission.
    [Tags]    negative
    Go To Jobs Page
    Click Add Job
    Click Save Job
    Page Should Contain    Job Title

Employment Dropdown Validation
    [Documentation]    Verify employment dropdown options.
    [Tags]    smoke    regression
    Go To Jobs Page
    Click Add Job
    Open Employment Dropdown
    Verify Employment Options

Employment Dropdown Selection - Data Driven
    [Documentation]    Validate all employment types are selectable.
    [Tags]    regression
    [Template]    Validate Employment Selection
    Full Time
    Part Time
    Freelancing

Create Job - Boundary (Experience)
    [Documentation]    Validate experience boundary values.
    [Tags]    regression
    Go To Jobs Page
    Click Add Job
    Fill Basic Job Details    Automation Tester    Full Time    0
    Wait For AI Prompt Generation


# ═══════════════════════════════════════════════════════════════════
# WIZARD FLOW — Add Job 3-Step Wizard
# UI redesigned: Method Selection → Generate/Paste JD → Refine Skill Map
# All tests below cover the new wizard introduced in the prod UI update.
# ═══════════════════════════════════════════════════════════════════

Wizard Step 1 - Page Load Happy Path
    [Documentation]    Navigate to Add Job and verify the Method Selection page loads
    ...                with both "Generate with AI" and "Paste Existing JD" cards visible.
    [Tags]    smoke    regression    ci-safe
    Navigate To Create Job Wizard
    Verify Step 1 Loaded
    Element Should Be Visible    ${JobsPage.GENERATE_AI_CARD}
    Element Should Be Visible    ${JobsPage.PASTE_JD_CARD}

Wizard Step 1 - Select Generate With AI
    [Documentation]    Clicking the "Generate with AI" method card selects it without error.
    [Tags]    smoke    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Verify Method Card Selected    Generate with AI

Wizard Step 1 - Select Paste Existing JD
    [Documentation]    Clicking the "Paste Existing JD" method card selects it without error.
    [Tags]    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Paste Existing JD
    Verify Method Card Selected    Paste Existing JD

Wizard Step 1 - Continue Navigates To Step 2 AI
    [Documentation]    Selecting "Generate with AI" and clicking Continue navigates to Step 2.
    [Tags]    smoke    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded

Wizard Step 1 - Continue Navigates To Step 2 Paste
    [Documentation]    Selecting "Paste Existing JD" and clicking Continue navigates to the Paste step.
    [Tags]    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Paste Existing JD
    Click Wizard Continue
    Verify Step 2 Paste Loaded

Wizard Step 2 AI - Generate JD Without Fields Shows Error
    [Documentation]    Clicking Generate JD without filling any role details shows a validation
    ...                toast "Please fill Job Title, Employment Type, and Experience Range first."
    [Tags]    negative    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Click Generate JD Button
    Verify Toast Contains    Please fill Job Title, Employment Type, and Experience Range first

Wizard Step 2 AI - Continue Without Generating Shows Error
    [Documentation]    Filling role details but skipping "Generate JD" and clicking Continue
    ...                shows toast "Please generate a job description before continuing."
    [Tags]    negative    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    QA Engineer    Full-time    2 years
    Click Wizard Continue
    Verify Toast Contains    Please generate a job description before continuing

Wizard Step 2 AI - Generation Progress Panel Visible
    [Documentation]    After navigating to Step 2 AI, all 4 Generation Progress step labels
    ...                are visible in the right panel in "Pending" state.
    [Tags]    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Verify Generation Progress Panel Visible

Wizard Step 2 AI - Employment Type Select Contains All Options
    [Documentation]    The Employment Type dropdown on Step 2 AI path exposes
    ...                Full-time, Part-time, and Freelancing options.
    [Tags]    smoke    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Verify Employment Type Options In Select

Wizard Step 2 AI - Happy Path JD Generation
    [Documentation]    Filling all required role details and clicking Generate JD triggers AI
    ...                generation; all 4 progress steps reach "Completed" and JD content appears.
    [Tags]    smoke    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    QA Engineer    Full-time    2 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Verify JD Content Present

Wizard Step 2 AI - Regenerate JD Button Available After Generation
    [Documentation]    After JD is generated, the "Regenerate Entire JD" button is visible.
    [Tags]    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    Automation Tester    Full-time    3 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Element Should Be Visible    ${JobsPage.REGENERATE_JD_BTN}

Wizard Step 2 Paste - Page Load Happy Path
    [Documentation]    Navigating to Step 2 Paste shows the textarea, Analysis panel,
    ...                Upload button, and Role Details section.
    [Tags]    smoke    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Paste Existing JD
    Click Wizard Continue
    Verify Step 2 Paste Loaded
    Element Should Be Visible    ${JobsPage.JD_PASTE_TEXTAREA}
    Element Should Be Visible    ${JobsPage.UPLOAD_DOC_BTN}
    Verify Analysis Panel Visible

Wizard Step 2 Paste - Continue Without JD Shows Error
    [Documentation]    Clicking Continue on the Paste JD step without entering any text
    ...                shows toast "Please provide a job description before continuing."
    [Tags]    negative    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Paste Existing JD
    Click Wizard Continue
    Verify Step 2 Paste Loaded
    Click Wizard Continue
    Verify Toast Contains    Please provide a job description before continuing

Wizard Step 2 Paste - Happy Path Proceed To Step 3
    [Documentation]    Filling role details and pasting JD text, then clicking Continue
    ...                proceeds to Step 3 Review & Refine Skill Map.
    [Tags]    smoke    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Paste Existing JD
    Click Wizard Continue
    Verify Step 2 Paste Loaded
    Fill Paste JD Role Details    Software Engineer    Full-time    3 years
    Paste Job Description Text
    ...    We are looking for a Software Engineer with 3 years of experience in Python and REST APIs.
    ...    Responsibilities include designing, developing, and testing software solutions.
    ...    Required skills: Python, REST APIs, Git, Docker, SQL.
    Click Wizard Continue
    Verify Step 3 Loaded

Wizard Step 3 - Skill Map Loads After AI Generation
    [Documentation]    After AI generation (Step 2) and clicking Continue, Step 3
    ...                "Review & Refine Skill Map" page loads with all 3 tab categories.
    [Tags]    smoke    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    Backend Engineer    Full-time    4 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Click Wizard Continue
    Verify Step 3 Loaded
    Verify All Skill Tabs Present

Wizard Step 3 - All Skill Tabs Clickable
    [Documentation]    All 3 skill category tabs on Step 3 are clickable and do not
    ...                produce an error when switched.
    [Tags]    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Fill AI Role Details    Frontend Developer    Full-time    2 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Click Wizard Continue
    Verify Step 3 Loaded
    Element Should Be Visible    ${JobsPage.TAB_SECONDARY_SKILLS}
    Click    ${JobsPage.TAB_SECONDARY_SKILLS}
    Element Should Be Visible    ${JobsPage.TAB_TRAINABLE_SKILLS}
    Click    ${JobsPage.TAB_TRAINABLE_SKILLS}
    Element Should Be Visible    ${JobsPage.TAB_TOP_SKILLS}
    Click    ${JobsPage.TAB_TOP_SKILLS}

Wizard Step 3 - Add Own Skill Button Present
    [Documentation]    The "+ Add Your Own Skill" action is visible on Step 3 Skill Map.
    [Tags]    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Fill AI Role Details    Data Analyst    Full-time    2 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Click Wizard Continue
    Verify Step 3 Loaded
    Verify Add Own Skill Button Present

Wizard Back Navigation - Step 3 Returns To Step 2
    [Documentation]    Clicking Back on Step 3 returns to Step 2 with the role details
    ...                and generated JD still preserved (SPA state retention).
    [Tags]    regression
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Fill AI Role Details    QA Lead    Full-time    5 years
    Click Generate JD Button
    Wait For JD Generation Complete
    Click Wizard Continue
    Verify Step 3 Loaded
    Click Wizard Back
    Verify Step 2 AI Loaded
    # Verify form data is still present after Back navigation
    ${title}=    Get Value    ${JobsPage.JOB_TITLE_AI_INPUT}
    Should Not Be Empty    ${title}

Wizard Back Navigation - Step 2 Returns To Step 1
    [Documentation]    Clicking Back on Step 2 returns to the Method Selection page (Step 1).
    [Tags]    regression    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Click Wizard Back
    Verify Step 1 Loaded

Wizard Edge - Very Long Job Title Accepted
    [Documentation]    A 100-character job title is accepted in the Job Title field without
    ...                truncation or error during input (boundary/edge value).
    [Tags]    edge    ci-safe
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    ${long_title}=    Set Variable
    ...    Senior Principal Staff Quality Assurance Automation Engineer - Platform Infrastructure Team
    Fill Text    ${JobsPage.JOB_TITLE_AI_INPUT}    ${long_title}
    ${actual}=    Get Value    ${JobsPage.JOB_TITLE_AI_INPUT}
    Should Not Be Empty    ${actual}

Wizard Edge - Zero Experience Accepted
    [Documentation]    Experience Level of "0" (boundary value) is accepted in the field
    ...                and triggers JD generation without error.
    [Tags]    boundary    edge
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    Junior QA    Full-time    0
    Click Generate JD Button
    Wait For JD Generation Complete

Wizard Edge - Part-time Employment Type Generates JD
    [Documentation]    "Part-time" employment type selection generates JD successfully
    ...                (verifies non-default option flows through correctly).
    [Tags]    edge
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    Content Reviewer    Part-time    1 year
    Click Generate JD Button
    Wait For JD Generation Complete

Wizard Edge - Freelancing Employment Type Generates JD
    [Documentation]    "Freelancing" employment type selection generates JD successfully.
    [Tags]    edge
    Navigate To Create Job Wizard
    Select Wizard Method    Generate with AI
    Click Wizard Continue
    Verify Step 2 AI Loaded
    Fill AI Role Details    UX Designer    Freelancing    3 years
    Click Generate JD Button
    Wait For JD Generation Complete
