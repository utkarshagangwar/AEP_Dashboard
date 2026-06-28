*** Settings ***
Library    SeleniumLibrary
Library    Dialogs
Suite Setup    Open InterviewGod
Suite Teardown    Close Browser

*** Variables ***
${URL}    https://pre-prod.interviewgod.ai
${BROWSER}    Chrome
${SLEEP}    2
${VALID_EMAIL}    test2@interviewgod.ai
${INVALID_EMAIL}    test@@god
${OTP_VALID}    123456
${OTP_INVALID}    111111

${LOGIN_LINK}      xpath=//a[@href="/login"]
${EMAIL_FIELD}    xpath=//input[contains(@type,'text') and contains(@placeholder,'Work email address')]
${SUBMIT_BTN}      xpath=//button[contains(@class,'bg-black') or contains(.,'Continue')]
${WORKSPACE_NAME}    xpath=//div[contains(@class,'text-gray-500') and contains(.,' members')]/ancestor::div[contains(@class,'rounded-lg')]
${DASHBOARD_NAME}    xpath=//p[contains(@class,'text-neutral-400') and contains(text(),'Welcome back! Highlights at a glance.')]
${OTP_FIELD}       xpath=//input[contains(@type,'text') and contains(@placeholder,'Code')]
${ERROR_EMAIL}     xpath=//p[contains(@class,'text-red-500') and contains(text(),'Invalid')]
${ERROR_OTP}     xpath=//p[contains(@class,'text-red-500') and contains(text(),'OTP')]
${RESEND_BTN}      xpath=//*[contains(text(),'Resend')]

*** Keywords ***
Open InterviewGod
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window
    Wait Until Element Is Visible    ${LOGIN_LINK}    15s
    Click Element    ${LOGIN_LINK}

Enter Email And Continue
    [Arguments]    ${email}
    Wait Until Element Is Visible    ${EMAIL_FIELD}    10s
    Clear Element Text    ${EMAIL_FIELD}
    Input Text    ${EMAIL_FIELD}    ${email}
    Click Element    ${SUBMIT_BTN}

Solve CAPTCHA
    Pause Execution    Solve CAPTCHA in browser then click OK
    Sleep    ${SLEEP}
    Click Element    ${SUBMIT_BTN}

Enter OTP And Submit
    [Arguments]    ${otp}
    Wait Until Element Is Visible    ${OTP_FIELD}    15s
    Clear Element Text    ${OTP_FIELD}
    Input Text    ${OTP_FIELD}    ${otp}
    Click Element    ${SUBMIT_BTN}

Login With Valid User
    Enter Email And Continue    ${VALID_EMAIL}
    Solve CAPTCHA
    Enter OTP And Submit    ${OTP_VALID}
    Wait Until Element Is Visible    ${WORKSPACE_NAME}    30s

Reset To Login
    Go To    ${URL}
    Wait Until Element Is Visible    ${LOGIN_LINK}    30s
    Click Element    ${LOGIN_LINK}

*** Test Cases ***

RG01 Invalid Email
    Enter Email And Continue    ${INVALID_EMAIL}
    Wait Until Element Is Visible    ${ERROR_EMAIL}    10s
    Element Should Be Visible    ${ERROR_EMAIL}
    Sleep    ${SLEEP}

RG02 Empty Email Blocked
    Reset To Login
    Wait Until Element Is Visible    ${EMAIL_FIELD}    10s
    Enter Email And Continue    ${EMPTY}
    Wait Until Element Is Visible    ${ERROR_EMAIL}    10s
    Element Should Be Visible    ${ERROR_EMAIL}
    Sleep    ${SLEEP}

RG03 Valid Email
    Reset To Login
    Wait Until Element Is Visible    ${EMAIL_FIELD}    10s
    Enter Email And Continue    ${VALID_EMAIL}
    Element Should Be Visible    ${SUBMIT_BTN}
    Sleep    ${SLEEP}

RG04 CAPTCHA Must Be Solved
    Wait Until Element Is Visible    ${EMAIL_FIELD}    10s
    Enter Email And Continue    ${VALID_EMAIL}
    Solve CAPTCHA

RG05 Wrong OTP Rejected
    Wait Until Element Is Visible    ${OTP_FIELD}    10s
    Enter OTP And Submit    ${OTP_INVALID}
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    10

RG06 Blank OTP Blocked
    Wait Until Element Is Visible    ${OTP_FIELD}    10s
    Enter OTP And Submit    ${EMPTY}
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    5

RG07 Less Than 6 Digit OTP Blocked
    Wait Until Element Is Visible    ${OTP_FIELD}    10s
    Enter OTP And Submit    123
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    5

RG08 More Than 6 Digit OTP Blocked
    Wait Until Element Is Visible    ${OTP_FIELD}    10s
    Enter OTP And Submit    1234567
    Wait Until Element Is Visible    ${ERROR_OTP}    10s
    Sleep    ${SLEEP}

RG09 Login With Correct OTP
    Wait Until Element Is Visible    ${OTP_FIELD}    10s
    Clear Element Text    ${OTP_FIELD}
    Input Text    ${OTP_FIELD}    ${OTP_VALID}
    Click Element    ${SUBMIT_BTN}
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
    Go To    ${URL}/login
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s
    Sleep    ${SLEEP}

RG14 Protected Page Without Login Redirects
    Reset To Login
    Go To    ${URL}
    Wait Until Element Is Visible    ${LOGIN_LINK}    10s
    Sleep    ${SLEEP}

RG15 New Tab Keeps Session
    Execute Javascript    window.open('${URL}/dashboard');
    Switch Window    NEW
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s
    Sleep    ${SLEEP}
    Switch Window    MAIN
