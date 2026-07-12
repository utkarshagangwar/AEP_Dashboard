*** Settings ***
Resource    ../common/browser.resource
Library     Browser


*** Variables ***
${BASE_URL}    https://vidya.online


*** Keywords ***
Navigate To Home Page
    Go To    ${BASE_URL}
    Wait For Page To Load

Navigate To Features Page
    Go To    ${BASE_URL}/features/personalized-roadmap
    Wait For Page To Load

Navigate To Features personalized-roadmap Page
    Go To    ${BASE_URL}/features/personalized-roadmap
    Wait For Page To Load

Navigate To Features ai-tutoring Page
    Go To    ${BASE_URL}/features/ai-tutoring
    Wait For Page To Load

Navigate To Features ai-video-lessons Page
    Go To    ${BASE_URL}/features/ai-video-lessons
    Wait For Page To Load

Navigate To Features evaluation-and-readiness Page
    Go To    ${BASE_URL}/features/evaluation-and-readiness
    Wait For Page To Load

Navigate To Features career-outcomes Page
    Go To    ${BASE_URL}/features/career-outcomes
    Wait For Page To Load

Navigate To Courses Page
    Go To    ${BASE_URL}/courses
    Wait For Page To Load

Navigate To Students Page
    Go To    ${BASE_URL}/students
    Wait For Page To Load

Navigate To Business Page
    Go To    ${BASE_URL}/business
    Wait For Page To Load

Navigate To Universities Page
    Go To    ${BASE_URL}/university
    Wait For Page To Load

Navigate To Contact Page
    Go To    ${BASE_URL}/contact-us
    Wait For Page To Load

Navigate To career opportunities Page
    Go To    ${BASE_URL}/jobs
    Wait For Page To Load

Navigate To free-access Page
    Go To    ${BASE_URL}/free-access
    Wait For Page To Load

Verify Current Page Is
    [Arguments]    ${expected_title}
    ${title}=    Get Title
    Should Contain    ${title}    ${expected_title}

Wait For Page To Load
    Wait For Load State    networkidle

Open Website
    New Browser    chromium    headless=False
    New Page       ${BASE_URL}

Verify Navigation Link
    [Arguments]    ${locator}    ${expected_url}
    Click    ${locator}
    Wait For Load State
    Get Url    ==    ${expected_url}
