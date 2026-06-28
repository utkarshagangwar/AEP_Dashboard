*** Settings ***
Documentation    Page Object Model for InterviewGod Login Page
...    This file contains all locators and actions specific to the login page

Library    SeleniumLibrary

*** Variables ***
# Login Page Locators
${LOGIN.LINK}              xpath=//a[@href="/login"]
${LOGIN.EMAIL_INPUT}       id=base-input
${LOGIN.SUBMIT_BTN}        xpath=//button[contains(@class,'bg-black') or contains(.,'Continue')]
${LOGIN.ERROR_EMAIL}       xpath=//p[contains(@class,'text-red-500') and contains(text(),'Invalid')]

# OTP Page Locators
${OTP.INPUT_FIELD}         id=base-input
${OTP.SUBMIT_BTN}          xpath=//button[contains(@class,'bg-black') or contains(.,'Continue')]
${OTP.ERROR_MESSAGE}       xpath=//p[contains(@class,'text-red-500') and contains(text(),'OTP')]
${OTP.RESEND_BTN}          xpath=//*[contains(text(),'Resend')]

# Workspace/Dashboard Locators
${WORKSPACE.NAME}          xpath=//div[contains(@class,'text-gray-500') and contains(.,' members')]/ancestor::div[contains(@class,'rounded-lg')]
${DASHBOARD.TITLE}         xpath=//p[contains(@class,'text-slate-800') and contains(text(),'All Hiring groups')]
${DASHBOARD.CREATE_BTN}    xpath=//button[contains(., 'Create Hiring Group')]

*** Keywords ***
# Navigation Actions
Navigate To Login Page
    [Documentation]    Navigates to the login page and waits for it to load
    [Arguments]    ${base_url}
    Go To    ${base_url}/login
    Wait Until Page Contains Element    ${LOGIN.EMAIL_INPUT}    timeout=10s

Click Login Link
    [Documentation]    Clicks the login link from homepage
    Wait Until Element Is Visible    ${LOGIN.LINK}    timeout=10s
    Click Element    ${LOGIN.LINK}

# Email Input Actions
Input Email Address
    [Documentation]    Enters email address in the input field
    [Arguments]    ${email}
    Wait Until Element Is Visible    ${LOGIN.EMAIL_INPUT}    timeout=10s
    Clear Element Text    ${LOGIN.EMAIL_INPUT}
    Input Text    ${LOGIN.EMAIL_INPUT}    ${email}

Click Email Submit Button
    [Documentation]    Clicks the submit/continue button on email page
    Wait Until Element Is Enabled    ${LOGIN.SUBMIT_BTN}    timeout=5s
    Click Element    ${LOGIN.SUBMIT_BTN}

Submit Email
    [Documentation]    Combined action: input email and submit
    [Arguments]    ${email}
    Input Email Address    ${email}
    Click Email Submit Button

# Email Verification
Email Error Should Be Visible
    [Documentation]    Verifies email validation error is displayed
    Wait Until Element Is Visible    ${LOGIN.ERROR_EMAIL}    timeout=10s
    Element Should Be Visible    ${LOGIN.ERROR_EMAIL}

Email Submit Should Be Disabled
    [Documentation]    Verifies submit button is disabled
    Element Should Be Disabled    ${LOGIN.SUBMIT_BTN}

Email Submit Should Be Enabled
    [Documentation]    Verifies submit button is enabled
    Element Should Be Enabled    ${LOGIN.SUBMIT_BTN}

# OTP Input Actions
Input OTP Code
    [Documentation]    Enters OTP code in the input field
    [Arguments]    ${otp}
    Wait Until Element Is Visible    ${OTP.INPUT_FIELD}    timeout=15s
    Clear Element Text    ${OTP.INPUT_FIELD}
    Input Text    ${OTP.INPUT_FIELD}    ${otp}

Click OTP Submit Button
    [Documentation]    Clicks the submit button on OTP page
    Wait Until Element Is Enabled    ${OTP.SUBMIT_BTN}    timeout=5s
    Click Element    ${OTP.SUBMIT_BTN}

Submit OTP
    [Documentation]    Combined action: input OTP and submit
    [Arguments]    ${otp}
    Input OTP Code    ${otp}
    Click OTP Submit Button

Click Resend OTP
    [Documentation]    Clicks the resend OTP button
    Wait Until Element Is Visible    ${OTP.RESEND_BTN}    timeout=10s
    Click Element    ${OTP.RESEND_BTN}

# OTP Verification
OTP Error Should Be Visible
    [Documentation]    Verifies OTP validation error is displayed
    Wait Until Element Is Visible    ${OTP.ERROR_MESSAGE}    timeout=10s
    Element Should Be Visible    ${OTP.ERROR_MESSAGE}

OTP Page Should Be Visible
    [Documentation]    Verifies user is on OTP page
    Wait Until Element Is Visible    ${OTP.INPUT_FIELD}    timeout=15s
    Page Should Contain Element    ${OTP.INPUT_FIELD}

Resend Button Should Be Visible
    [Documentation]    Verifies resend OTP button is present
    Element Should Be Visible    ${OTP.RESEND_BTN}

# Workspace/Dashboard Actions
Click Workspace
    [Documentation]    Clicks on workspace to navigate to dashboard
    Wait Until Element Is Visible    ${WORKSPACE.NAME}    timeout=30s
    Click Element    ${WORKSPACE.NAME}

# Workspace/Dashboard Verification
Workspace Should Be Visible
    [Documentation]    Verifies workspace is displayed after successful login
    Wait Until Element Is Visible    ${WORKSPACE.NAME}    timeout=30s
    Element Should Be Visible    ${WORKSPACE.NAME}

Dashboard Should Be Visible
    [Documentation]    Verifies dashboard is displayed
    Wait Until Element Is Visible    ${DASHBOARD.TITLE}    timeout=10s
    Element Should Be Visible    ${DASHBOARD.TITLE}

Create Hiring Group Button Should Be Visible
    [Documentation]    Verifies create button is visible on dashboard
    Wait Until Element Is Visible    ${DASHBOARD.CREATE_BTN}    timeout=10s
    Element Should Be Visible    ${DASHBOARD.CREATE_BTN}

User Should Be Logged In
    [Documentation]    Verifies user is logged in by checking workspace visibility
    Workspace Should Be Visible

User Should Not Be On Login Page
    [Documentation]    Verifies user has navigated away from login page
    Page Should Not Contain Element    ${LOGIN.EMAIL_INPUT}

# Complete Flows
Complete Email Step
    [Documentation]    Completes the email input step with valid email
    [Arguments]    ${email}
    Submit Email    ${email}
    # Add CAPTCHA handling here when automated

Complete OTP Step
    [Documentation]    Completes the OTP input step
    [Arguments]    ${otp}
    Submit OTP    ${otp}
    Workspace Should Be Visible

Complete Full Login
    [Documentation]    Performs complete login flow from start to finish
    [Arguments]    ${email}    ${otp}
    Submit Email    ${email}
    # Add CAPTCHA handling here when automated
    Sleep    5s    # Placeholder for CAPTCHA
    Submit OTP    ${otp}
    Workspace Should Be Visible
