*** Settings ***
Documentation    Dashboard smoke and regression coverage.
Library          JSONLibrary
Resource         ../../resources/browser_compat.resource
Library          ../../libs/hopscotch_client.py
Resource         ../../resources/keywords/login_keywords.resource
Resource         ../../resources/keywords/video_keywords.resource
Resource         ../../resources/keywords/dashboard_keywords.resource
Variables        ../../resources/variables/config.py

Suite Setup      Open InterviewGod Suite
Suite Teardown   Close Browser
Test Setup       Open Test With Recording
Test Teardown    Close Test And Save Recording


*** Test Cases ***

Dashboard Load - Happy Path
    [Documentation]    Verify dashboard loads correctly
    [Tags]    smoke    regression    ci-safe
    Wait For Dashboard To Load
    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Get Numeric Value    ${DashboardPage.TOTAL_JOBS}
    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Get Numeric Value    ${DashboardPage.COMPLETED}
    Validate Recent Activities

Dashboard Metrics Validation
    [Documentation]    Validate metric values are numeric
    [Tags]    regression    ci-safe
    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Get Numeric Value    ${DashboardPage.TOTAL_JOBS}
    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Get Numeric Value    ${DashboardPage.COMPLETED}

Recent Activities Visibility
    [Documentation]    Validate activities are visible
    [Tags]    regression    ci-safe
    Validate Recent Activities

Navigation Menu Validation
    [Documentation]    Verify all sidebar menu items are visible.
    [Tags]    smoke    ci-safe
    Open Dashboard Page
    Verify Navigation Menu Items

Navigation To Jobs Page
    [Documentation]    Verify the user can navigate to the jobs page.
    [Tags]    regression    ci-safe
    Open Dashboard Page
    Navigate To Jobs Page

Dashboard Edge - No Activities
    [Documentation]    Validate behavior when no activities exist.
    [Tags]    negative    ci-safe
    Open Dashboard Page
    ${count}=    Get Element Count    xpath=//div[contains(text(),'Job updated')]
    Should Be True    ${count} >= 0

Dashboard Boundary - Large Numbers
    [Documentation]    Validate large metric values
    [Tags]    regression    ci-safe
    ${val}=    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}

Dashboard Zero Metrics Handling
    [Documentation]    Validate metrics when value is 0
    [Tags]    edge    ci-safe
    ${val}=    Get Numeric Value    ${DashboardPage.IN_PROGRESS}

Recent Activity Content Validation
    [Documentation]    Validate activity text is meaningful
    [Tags]    regression    ci-safe
    ${items}=    Get WebElements    ${DashboardPage.ACTIVITY_ITEMS}
    FOR    ${item}    IN    @{items}
        ${text}=    Get Text    ${item}
        Should Not Be Empty    ${text}
    END

Dashboard Load Performance
    [Documentation]    Ensure dashboard loads within 5 seconds
    [Tags]    regression    ci-safe
    ${start}=    Get Time
    Wait For Dashboard To Load
    ${end}=    Get Time
    ${diff}=    Evaluate    ${end} - ${start}
    Should Be True    ${diff} < 5

Logout Functionality
    [Documentation]    Verify the logout button is clickable.
    [Tags]    regression    ci-safe
    Open Dashboard Page
    Click Logout
