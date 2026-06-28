*** Settings ***
Documentation    Dashboard smoke and regression coverage – InterviewGod v2.5.2
...
...              SOW reference: Section 17 (Admin Dashboard) and Section 119-128
...              (Hiring Groups Dashboard / Minute Balance).
...              All test cases are read-only; no data mutations are performed.
Library          JSONLibrary
Library          SeleniumLibrary
Resource        ../InterviewGod/CI_CD/resources/keywords/dashboard_keywords.resource
Resource        ../InterviewGod/CI_CD/resources/keywords/login_keywords.resource
Variables        ../InterviewGod/CI_CD/pages/dashboard_page.py
Variables        ../InterviewGod/CI_CD/resources/variables/config.py

Suite Setup      Open Browser    ${BASE_URL}    ${BROWSER}
Suite Teardown    Close Browser

*** Test Cases ***

# ── Login / session ─────────────────────────────────────────────
Open InterviewGod and Login With Valid User
    [Documentation]    Load IG
    Maximize Browser Window
    Wait Until Element Is Visible    ${LOGIN_LINK}    15s
    Click Element    ${LOGIN_LINK}
    Enter Email And Continue    ${VALID_EMAIL}
    Solve CAPTCHA
    Sleep    ${SLEEP}
    Enter OTP And Submit    ${OTP_VALID}
    Wait Until Element Is Visible    ${WORKSPACE_NAME}    30s

Workspace Loads
    [Documentation]    Load workspace
    Click Element    ${WORKSPACE_NAME}
    Wait Until Element Is Visible    ${DASHBOARD_NAME}    10s

Dashboard Load - Happy Path
    [Documentation]    Verify the dashboard loads with all data.
    Wait Until Element Is Visible    ${DashboardPage.PAGE_TITLE}    timeout=20s
    Wait Until Element Is Visible    ${DashboardPage.CREDIT_BALANCE}    timeout=15s
    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Get Numeric Value    ${DashboardPage.TOTAL_JOBS}
    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Get Numeric Value    ${DashboardPage.COMPLETED}

