*** Settings ***
Library    Browser
Library    BuiltIn
Library    DateTime


*** Variables ***
${BROWSER}              chromium
${HEADLESS}             ${FALSE}
${DEFAULT_TIMEOUT}      70s
${VIEWPORT_W}           1440
${VIEWPORT_H}           900


*** Keywords ***
Open Browser Session
    New Browser    browser=${BROWSER}    headless=${HEADLESS}
    New Context    viewport={'width': ${VIEWPORT_W}, 'height': ${VIEWPORT_H}}
    New Page
    Set Default Timeout

Close Browser Session
    Close Browser

Take Screenshot On Fail
    ${timestamp}=    Get Current Date    result_format=%Y%m%d_%H%M%S
    Take Screenshot    filename=screenshots/fail_${timestamp}.png

Set Default Timeout
    Set Browser Timeout    ${DEFAULT_TIMEOUT}

Maximize Browser Window
    Set Viewport Size    1920    1080

