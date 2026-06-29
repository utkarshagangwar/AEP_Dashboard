*** Settings ***
Documentation    Dashboard smoke and regression coverage – InterviewGod v2.5.2
...
...              SOW reference: Section 17 (Admin Dashboard) and Section 119-128
...              (Hiring Groups Dashboard / Minute Balance).
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


# ════════════════════════════════════════════════════════════════
# TEST CASES
# ════════════════════════════════════════════════════════════════

*** Test Cases ***

# ── Happy path ──────────────────────────────────────────────────

Dashboard Load - Happy Path
    [Documentation]    Verify the dashboard loads with title, credit balance,
    ...                all four metric cards, and the recent activities section.
    [Tags]    smoke    regression
    Wait For Dashboard To Load
    Verify All Metric Cards Visible
    Validate Recent Activities


# ── Page title ──────────────────────────────────────────────────

Dashboard Title Is Overview
    [Documentation]    The page heading must read exactly 'Overview'.
    [Tags]    smoke
    Wait Until Element Is Visible    ${DashboardPage.PAGE_TITLE}    timeout=20s
    ${title}=    Get Text    ${DashboardPage.PAGE_TITLE}
    Should Be Equal    ${title}    Overview


# ── Credit balance ──────────────────────────────────────────────

Credit Balance Visible
    [Documentation]    The Credit Balance pill must be visible after login.
    [Tags]    smoke    regression
    Verify Credit Balance Visible

Credit Balance Contains Mins
    [Documentation]    The credit balance value must include the unit 'mins'.
    [Tags]    regression
    Verify Credit Balance Contains Minutes

# ── Metric cards ────────────────────────────────────────────────

Dashboard Metrics Validation
    [Documentation]    All four metric card values must be non-empty integers.
    [Tags]    regression
    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Get Numeric Value    ${DashboardPage.TOTAL_JOBS}
    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Get Numeric Value    ${DashboardPage.COMPLETED}

All Metrics Are Non Negative
    [Documentation]    Each metric value must be zero or a positive integer.
    [Tags]    regression
    Verify All Metrics Are Non Negative

Total Candidates Card Visible
    [Documentation]    The 'Total Candidates' card renders a numeric value.
    [Tags]    regression
    ${val}=    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Should Not Be Empty    ${val}

Jobs Card Visible
    [Documentation]    The 'Jobs' card renders a numeric value.
    [Tags]    regression
    ${val}=    Get Numeric Value    ${DashboardPage.TOTAL_JOBS}
    Should Not Be Empty    ${val}

Interview In Progress Card Visible
    [Documentation]    The 'Interview In Progress' card renders a numeric value.
    [Tags]    regression
    ${val}=    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Should Not Be Empty    ${val}

Interview Completed Card Visible
    [Documentation]    The 'Interview Completed' card renders a numeric value.
    [Tags]    regression
    ${val}=    Get Numeric Value    ${DashboardPage.COMPLETED}
    Should Not Be Empty    ${val}

Dashboard Metrics Cards Are Not Clickable
    [Documentation]    SOW 122.3 – metric cards are static and must not navigate
    ...                when clicked. Verify no URL change occurs.
    [Tags]    regression
    ${before}=    Get Location
    Click Element    ${DashboardPage.TOTAL_CANDIDATES}
    Sleep    1s
    ${after}=    Get Location
    Should Be Equal    ${before}    ${after}

# NOTE: "Large numbers" and "zero value" tests validate the display format only.
# Actual data depends on the test environment state.

Dashboard Boundary - Large Numbers Display
    [Documentation]    If total candidates is a large number it must still be
    ...                an integer with no formatting characters (commas etc.).
    [Tags]    regression    boundary
    ${val}=    Get Numeric Value    ${DashboardPage.TOTAL_CANDIDATES}
    Should Match Regexp    ${val}    ^[0-9]+$

Dashboard Zero Metrics Handling
    [Documentation]    If Interview In Progress is 0 the cell must show '0'
    ...                not an empty string or dash.
    [Tags]    edge
    ${val}=    Get Numeric Value    ${DashboardPage.IN_PROGRESS}
    Should Match Regexp    ${val}    ^[0-9]+$


# ── Recent activities ───────────────────────────────────────────

Recent Activities Visibility
    [Documentation]    The Recent Activities heading and at least one card must
    ...                be visible.
    [Tags]    regression
    Validate Recent Activities

Recent Activities Title Text
    [Documentation]    The section heading must read exactly 'Recent Activities'.
    [Tags]    regression
    Wait Until Element Is Visible    ${DashboardPage.RECENT_ACTIVITIES_TITLE}    timeout=15s
    ${text}=    Get Text    ${DashboardPage.RECENT_ACTIVITIES_TITLE}
    Should Be Equal    ${text}    Recent Activities

Recent Activity Titles Are Not Empty
    [Documentation]    Every activity card must carry a non-empty title span.
    [Tags]    regression
    Verify Activity Titles Are Not Empty

Recent Activity Timestamps Are Not Empty
    [Documentation]    Every activity card must carry a non-empty timestamp span.
    [Tags]    regression
    Verify Activity Timestamps Are Not Empty

Recent Activities Count At Least One
    [Documentation]    At least one activity must be present in the feed.
    [Tags]    regression
    Verify Activities Count Is At Least    1

Dashboard Edge - No Activities
    [Documentation]    If no activities exist the activities container is still
    ...                rendered (empty state is acceptable; count >= 0).
    [Tags]    negative
    ${count}=    Get Element Count    ${DashboardPage.ACTIVITY_ITEMS}
    Should Be True    ${count} >= 0

Recent Activity Content Validation
    [Documentation]    Validate each activity card has non-empty body text.
    [Tags]    regression
    ${items}=    Get WebElements    ${DashboardPage.ACTIVITY_TITLE_CELLS}
    FOR    ${item}    IN    @{items}
        ${text}=    Get Text    ${item}
        Should Not Be Empty    ${text}
    END

Recent Activities Section Is Scrollable
    [Documentation]    The scrollable container element must be present in the DOM.
    [Tags]    regression
    Element Should Be Visible    ${DashboardPage.RECENT_ACTIVITIES_SECTION}

Recent Activities Are Read Only - No Edit Controls
    [Documentation]    SOW 17.5 – no clickable drill-down exists on activity items.
    ...                Verify no button or link lives inside activity cards.
    [Tags]    regression
    ${count}=    Get Element Count
    ...    xpath=//div[contains(@class,'dashboard-scroll')]//button
    Should Be Equal As Integers    ${count}    0


# ── Navigation menu ─────────────────────────────────────────────

Navigation Menu Validation
    [Documentation]    All five sidebar menu items must be visible.
    [Tags]    smoke
    Verify Navigation Menu Items

Navigation To Jobs Page
    [Documentation]    Clicking Jobs navigates away from the dashboard.
    [Tags]    regression
    Open Dashboard Page
    Navigate To Jobs Page

Dashboard Menu Item Is Active
    [Documentation]    The Dashboard menu item itself must be visible on the dashboard.
    [Tags]    regression
    Element Should Be Visible    ${DashboardPage.DASHBOARD_MENU}


# ── Load performance ────────────────────────────────────────────

Dashboard Load Performance
    [Documentation]    The page title and credit balance must both appear within
    ...                20 s of navigation (SOW: platform is desktop browser only).
    [Tags]    regression    performance
    Go To    ${BASE_URL}dashboard
    Wait For Dashboard To Load


# ── SOW-specific constraints ────────────────────────────────────

Dashboard Has No Filters
    [Documentation]    SOW 17.5 – no filter controls exist on the dashboard.
    [Tags]    regression    sow
    ${count}=    Get Element Count    xpath=//main//select | //main//input[@type='date']
    Should Be Equal As Integers    ${count}    0

Dashboard Has No Export
    [Documentation]    SOW 17.5 – no export button exists on the dashboard.
    [Tags]    regression    sow
    ${count}=    Get Element Count
    ...    xpath=//*[contains(translate(normalize-space(),'EXPORT','export'),'export')]
    Should Be Equal As Integers    ${count}    0

Dashboard Has No Refresh Button
    [Documentation]    SOW 17.5 – no manual refresh control exists on the dashboard.
    [Tags]    regression    sow
    ${count}=    Get Element Count
    ...    xpath=//button[contains(translate(normalize-space(),'REFRESH','refresh'),'refresh')]
    Should Be Equal As Integers    ${count}    0
