*** Settings ***
Documentation    Jobs page smoke and regression coverage.
Library          JSONLibrary
Resource         ../../resources/browser_compat.resource
Resource         ../../resources/keywords/login_keywords.resource
Resource         ../../resources/keywords/jobs_keywords.resource
Variables        ../../resources/variables/config.py
Variables        ../../pages/login_page.py


Suite Setup      Open Browser    ${BASE_URL}    ${BROWSER}
Suite Teardown   Close Browser


*** Variables ***
${DATA_FILE}    ../../test_data/jobs_data.json


*** Test Cases ***
RG01 Invalid Email
    Enter Email And Continue    ${INVALID_EMAIL}
    Wait Until Element Is Visible    ${ERROR_EMAIL}    10s
    Element Should Be Visible    ${ERROR_EMAIL}
    Sleep    ${SLEEP}

RG02 Empty Email Blocked
    Reset To Login
    Wait Until Element Is Visible    ${EMAIL_INPUT}    10s
    Enter Email And Continue    ${EMPTY}
    Wait Until Element Is Visible    ${ERROR_EMAIL}    10s
    Element Should Be Visible    ${ERROR_EMAIL}
    Sleep    ${SLEEP}

RG03 Valid Email
    Reset To Login
    Wait Until Element Is Visible    ${EMAIL_INPUT}    10s
    Enter Email And Continue    ${VALID_EMAIL}
    Element Should Be Visible    ${CONTINUE_BTN}
    Sleep    ${SLEEP}

RG04 CAPTCHA Must Be Solved
    Wait Until Element Is Visible    ${EMAIL_INPUT}    10s
    Enter Email And Continue    ${VALID_EMAIL}
    Solve CAPTCHA

RG05 Wrong OTP Rejected
    Wait Until Element Is Visible    ${OTP_INPUT}    10s
    Enter OTP And Submit    ${OTP_INVALID}
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    10

RG06 Blank OTP Blocked
    Wait Until Element Is Visible    ${OTP_INPUT}    10s
    Enter OTP And Submit    ${EMPTY}
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    5

RG07 Less Than 6 Digit OTP Blocked
    Wait Until Element Is Visible    ${OTP_INPUT}    10s
    Enter OTP And Submit    123
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    5

RG08 More Than 6 Digit OTP Blocked
    Wait Until Element Is Visible    ${OTP_INPUT}    10s
    Enter OTP And Submit    1234567
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    ${SLEEP}

RG09 Login With Correct OTP
    Wait Until Element Is Visible    ${OTP_INPUT}    10s
    Clear Element Text    ${OTP_INPUT}
    Input Text    ${OTP_INPUT}    ${OTP_VALID}
    Click Element    ${CONTINUE_BTN}
    Sleep    ${SLEEP}

RG10 Successful Login Redirect to Workspace and name visible
    Wait Until Element Is Visible    ${WORKSPACE_NAME}    10s
    Element Should Be Visible    ${WORKSPACE_NAME}
    Sleep    ${SLEEP}

RG11 Workspace Loads
    Click Element    ${WORKSPACE_NAME}
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s

RG12 Session Persists After Refresh
    Reload Page
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s
    Sleep    ${SLEEP}

RG13 Cannot Access Login When Logged In
    Go To    ${LOGIN_LINK}/login
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s
    Sleep    ${SLEEP}

RG14 Protected Page Without Login Redirects
    Reset To Login
    Go To    ${LOGIN_LINK}
    Wait Until Element Is Visible    ${LOGIN_LINK}    10s
    Sleep    ${SLEEP}

RG15 New Tab Keeps Session
    Execute Javascript    window.open('${LOGIN_LINK}/dashboard');
    Switch Window    NEW
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s
    Sleep    ${SLEEP}
    Switch Window    MAIN
