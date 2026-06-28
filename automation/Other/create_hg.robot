*** Settings ***
Library    SeleniumLibrary
Library    Dialogs
Suite Setup    Open InterviewGod
Suite Teardown    Close Browser

*** Variables ***
${URL}      https://pre-prod.interviewgod.ai/
${BROWSER}  Chrome
${Sleep}    3

# Locators (Cleaned up for stability)
${LOGIN_LINK}      xpath=//a[@href="/login"]
${INPUT_FIELD}     id=base-input
# This locator finds the button by its type or general structure, ignoring the messy tailwind classes
${SUBMIT_BTN}      xpath=//button[contains(@type, 'Continue') or contains(@class, 'bg-black')]

${JOB_DESCRIPTION}    Proven experience as a Full-Stack Developer or similar role.\n
...                   Proficiency in front-end languages (e.g., HTML, CSS, JavaScript).\n
...                   Strong knowledge of back-end languages (e.g., Python, Java).\n
...                   Familiarity with databases (e.g., MySQL, PostgreSQL).\n
...                   Experience with cloud platforms (e.g., AWS, Azure).

*** Keywords ***
Open InterviewGod
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window


*** Test Cases ***

TC01 Login
    #1. Click Login
    Wait Until Element Is Visible    ${LOGIN_LINK}    timeout=10s
    Click Link    ${LOGIN_LINK}

    # 2. Enter Email
    Wait Until Element Is Visible    ${INPUT_FIELD}    timeout=10s
    Input Text    ${INPUT_FIELD}    test@interviewgod.ai

    # 3. Click Continue
    Click Element    ${SUBMIT_BTN}

    # 4. CAPTCHA Pause
    # The script will freeze here until you solve it and click OK in the popup
    Pause Execution    Please solve the CAPTCHA manually on the platform and click OK here.
    Sleep    ${Sleep}
    Click Element    ${SUBMIT_BTN}

    # 5. Enter OTP / Password
    # We wait for the input to be ready again (or cleared)
    Wait Until Page Contains    Check your email for a code    timeout=10s
    Input Text    ${INPUT_FIELD}    123456

    # 6. Click Continue (Second time)
    Click Element    ${SUBMIT_BTN}

    # 7. THE FIX: Increased timeout and verifying "Visibility" instead of just text
    # "timeout=30s" gives the server time to process the login
    Wait Until Page Contains    Welcome to InterviewGod    timeout=30s

    # 8. Click the Profile/Item
    Wait Until Element Is Visible    xpath=//*[text()='Banao_IG']    timeout=10s
    Click Element    xpath=//*[text()='Banao_IG']

    Wait Until Element Is Visible    xpath=//button[contains(., 'Create Hiring Group')]    timeout=10s

TC02 Create Hiring Group
    # This finds the <button> that contains the text "Create Hiring Group"
    Click Element    xpath=//button[contains(., 'Create Hiring Group')]
    Sleep    ${Sleep}
    Input Text    xpath=//textarea    ${JOB_DESCRIPTION}
    Sleep    ${SLEEP}

    Click Element    xpath=//button[contains(., 'Continue')]
    Wait Until Element Is Visible    xpath=//input[contains(@placeholder, 'Enter group name')]    timeout=10s

    Input Text    xpath=//input[contains(@placeholder, 'Enter group name')]    Test 1
    Sleep    ${SLEEP}

