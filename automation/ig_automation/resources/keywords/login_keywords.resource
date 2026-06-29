*** Settings ***
Documentation    Login helpers shared by UI suites.
Library          Collections
Library          Dialogs
Library          OperatingSystem
Resource         ../browser_compat.resource
Library          ../../libs/hopscotch_client.py
Variables        ../../resources/variables/config.py
Variables        ../../pages/login_page.py


*** Keywords ***
# ═══════════════════════════════════════════════════════════════════════════════
# SUITE A — CI/CD Bypass Login
# Use this keyword as Suite Setup in all regression/smoke/sanity suites.
#
# HOW IT WORKS:
#   Bypass Login And Open Session (inside hopscotch_client.py) handles:
#     - Opening the browser (via Browser library / Playwright)
#     - Calling POST /admin-login-by-api-key to get a fresh token
#     - Injecting the authToken cookie into the Browser context
#     - Navigating to /dashboard
#     - Asserting the dashboard loaded
#
# DO NOT call Open Browser before this keyword — it opens the browser itself.
# ═══════════════════════════════════════════════════════════════════════════════

Open InterviewGod With Bypass
    [Documentation]    Full CI/CD bypass login — no CAPTCHA, no UI form.
    ...                Opens browser, injects auth cookie, lands on dashboard.
    ...                Safe for headless CI runners.
    ...
    ...                Legacy Suite Setup keyword — opens one context for the whole suite.
    ...                Use Open InterviewGod Suite + video_keywords.resource instead
    ...                when per-test video recording is required.
    [Tags]             ci-safe
    Bypass Login And Open Session    ${TEST_EMAIL}    ${TEST_OTP}

Open InterviewGod Suite
    [Documentation]    Suite Setup keyword for the per-test-video architecture.
    ...                Fetches the auth token once and opens the Playwright browser.
    ...                Each test then opens its own context (with video recording)
    ...                via the Open Test With Recording keyword in video_keywords.resource.
    ...
    ...                Pair with:
    ...                  Suite Teardown   Close Browser
    ...                  Test Setup       Open Test With Recording
    ...                  Test Teardown    Close Test And Save Recording
    [Tags]             ci-safe
    Open Browser Only

# ═══════════════════════════════════════════════════════════════════════════════
# SUITE B — UI Login (manual / scheduled runs only)
# All keywords below are unchanged from original.
# Used by login_tests.robot which tests the actual UI login flow.
# Do NOT use in any regression suite running on CI.
# ═══════════════════════════════════════════════════════════════════════════════

Open InterviewGod
    New Browser    chromium    headless=False
    New Context    viewport={'width': 1920, 'height': 1080}
    New Page    ${BASE_URL}
    Wait Until Element Is Visible    ${LOGIN_LINK}    15s
    Click Element    ${LOGIN_LINK}

Enter Email And Continue
    [Arguments]    ${email}
    Wait Until Element Is Visible    ${EMAIL_INPUT}    10s
    Fill Text    ${EMAIL_INPUT}    ${email}
    Click    ${CONTINUE_BTN}

Solve CAPTCHA
    Pause Execution    Solve CAPTCHA in browser then click OK
    Sleep    ${SLEEP}
    Click    ${CONTINUE_BTN}

Enter OTP And Submit
    [Arguments]    ${otp}
    Wait Until Element Is Visible    ${OTP_INPUT}    15s
    Fill Text    ${OTP_INPUT}    ${otp}
    Click    ${CONTINUE_BTN}

Login With Valid User
    [Documentation]    Maximize window and Login With Valid User.
    Set Viewport Size    1920    1080
    Wait Until Element Is Visible    ${LOGIN_LINK}    15s
    Click Element    ${LOGIN_LINK}
    Sleep    ${SLEEP}
    Enter Email And Continue    ${VALID_EMAIL}
    Solve CAPTCHA
    Sleep    ${SLEEP}
    Enter OTP And Submit    ${OTP_VALID}
    Wait Until Element Is Visible    ${WORKSPACE_NAME}    30s
    Element Should Be Visible    ${WORKSPACE_NAME}
    Sleep    ${SLEEP}
    Click Element    ${WORKSPACE_NAME}
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    30s
    Sleep    ${SLEEP}

Reset To Login
    Go To    ${BASE_URL}
    Wait Until Element Is Visible    ${LOGIN_LINK}    30s
    Click Element    ${LOGIN_LINK}
