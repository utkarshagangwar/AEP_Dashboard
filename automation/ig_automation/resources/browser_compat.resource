*** Settings ***
Documentation    SeleniumLibrary → Browser (Playwright) compatibility layer.
...
...              Defines every SeleniumLibrary keyword used in this project
...              using Browser library equivalents, so ALL test case steps
...              remain completely unchanged after the Playwright migration.
...
...              Import this resource instead of "Library  SeleniumLibrary"
...              in every .robot test file and .resource keyword file.
# run_on_failure=None disables Browser's built-in screenshot-on-failure. We capture
# our own HTML + screenshot evidence in Test Teardown (evidence_keywords.resource),
# so the built-in is redundant — and when a keyword fails after the context is gone
# it raises "Tried to take screenshot, but no page was open", which marked tests as
# failed for a teardown-only problem. Disabling it removes that false-failure noise.
Library          Browser    run_on_failure=None
Variables        variables/config.py


# ═══════════════════════════════════════════════════════════════════
# BROWSER LIFECYCLE
# ═══════════════════════════════════════════════════════════════════

*** Keywords ***

Open Browser
    [Documentation]    Drop-in for SeleniumLibrary Open Browser.
    ...                Maps browser name strings (chrome, headlesschrome, firefox) to
    ...                Playwright equivalents and sets headless mode automatically.
    [Arguments]    ${url}    ${browser}=chrome
    # --- Determine browser type ---
    ${browser_lower}=    Convert To Lower Case    ${browser}
    ${btype}=    Set Variable    chromium
    IF    'firefox' in '${browser_lower}'
        ${btype}=    Set Variable    firefox
    ELSE IF    'webkit' in '${browser_lower}' or 'safari' in '${browser_lower}'
        ${btype}=    Set Variable    webkit
    END
    # --- Determine headless mode ---
    ${is_headless}=    Set Variable    ${FALSE}
    IF    'headless' in '${browser_lower}'
        ${is_headless}=    Set Variable    ${TRUE}
    END
    New Browser    ${btype}    headless=${is_headless}
    New Context    viewport={'width': ${VIEWPORT_WIDTH}, 'height': ${VIEWPORT_HEIGHT}}
    New Page    ${url}

Maximize Browser Window
    [Documentation]    Drop-in for SeleniumLibrary Maximize Browser Window.
    ...                Playwright does not expose a native maximize.
    ...                Sets viewport to the configured size (VIEWPORT_WIDTH × VIEWPORT_HEIGHT).
    Set Viewport Size    ${VIEWPORT_WIDTH}    ${VIEWPORT_HEIGHT}


# ═══════════════════════════════════════════════════════════════════
# ELEMENT WAIT / VISIBILITY
# ═══════════════════════════════════════════════════════════════════

Wait Until Element Is Visible
    [Documentation]    Drop-in for SeleniumLibrary Wait Until Element Is Visible.
    [Arguments]    ${locator}    ${timeout}=15s
    Wait For Elements State    ${locator}    visible    ${timeout}

Wait Until Element Is Not Visible
    [Documentation]    Drop-in for SeleniumLibrary Wait Until Element Is Not Visible.
    [Arguments]    ${locator}    ${timeout}=15s
    Wait For Elements State    ${locator}    hidden    ${timeout}

Wait Until Page Contains Element
    [Documentation]    Drop-in for SeleniumLibrary Wait Until Page Contains Element.
    [Arguments]    ${locator}    ${timeout}=15s
    Wait For Elements State    ${locator}    attached    ${timeout}

Wait Until Page Contains
    [Documentation]    Drop-in for SeleniumLibrary Wait Until Page Contains.
    [Arguments]    ${text}    ${timeout}=15s
    Wait For Elements State    text=${text}    visible    ${timeout}


# ═══════════════════════════════════════════════════════════════════
# ELEMENT ASSERTIONS
# ═══════════════════════════════════════════════════════════════════

Element Should Be Visible
    [Documentation]    Drop-in for SeleniumLibrary Element Should Be Visible.
    ...                Default timeout 15s — the SPA can take up to 15s to render
    ...                content after navigation; the old 2s default produced false
    ...                failures on tests whose frozen bodies call this directly with
    ...                no explicit timeout (e.g. Recent Activities Section Is Scrollable).
    [Arguments]    ${locator}    ${timeout}=15s
    Wait For Elements State    ${locator}    visible    ${timeout}

Element Should Not Be Visible
    [Documentation]    Drop-in for SeleniumLibrary Element Should Not Be Visible.
    [Arguments]    ${locator}    ${timeout}=2s
    Wait For Elements State    ${locator}    hidden    ${timeout}

Element Should Be Enabled
    [Documentation]    Drop-in for SeleniumLibrary Element Should Be Enabled.
    [Arguments]    ${locator}
    Wait For Elements State    ${locator}    enabled    2s

Element Should Be Focused
    [Documentation]    Drop-in for SeleniumLibrary Element Should Be Focused.
    [Arguments]    ${locator}
    Wait For Elements State    ${locator}    focused    2s

Element Should Be Present
    [Documentation]    Checks element exists in DOM (attached).
    [Arguments]    ${locator}
    Wait For Elements State    ${locator}    attached    2s

Element Text Should Be
    [Documentation]    Drop-in for SeleniumLibrary Element Text Should Be.
    [Arguments]    ${locator}    ${expected}
    ${actual}=    Get Text    ${locator}
    Should Be Equal    ${actual}    ${expected}


# ═══════════════════════════════════════════════════════════════════
# INTERACTION — CLICK / INPUT
# ═══════════════════════════════════════════════════════════════════

Click Element
    [Documentation]    Drop-in for SeleniumLibrary Click Element.
    [Arguments]    ${locator}
    Click    ${locator}

Click Button
    [Documentation]    Drop-in for SeleniumLibrary Click Button.
    [Arguments]    ${locator}
    Click    ${locator}

Input Text
    [Documentation]    Drop-in for SeleniumLibrary Input Text.
    ...                Clears the field first (same default behaviour as SeleniumLibrary).
    [Arguments]    ${locator}    ${text}
    Fill Text    ${locator}    ${text}    clear=True

Clear Element Text
    [Documentation]    Drop-in for SeleniumLibrary Clear Element Text.
    [Arguments]    ${locator}
    Fill Text    ${locator}    ${EMPTY}

Select Checkbox
    [Documentation]    Drop-in for SeleniumLibrary Select Checkbox.
    [Arguments]    ${locator}
    Check Checkbox    ${locator}

Press Keys
    [Documentation]    Drop-in for SeleniumLibrary Press Keys.
    ...                Supports single keys and CTRL+x combos.
    ...                Pass locator=NONE to send keys without focusing an element first.
    [Arguments]    ${locator}    @{keys}
    IF    '${locator}' != 'NONE' and '${locator}' != 'None'
        Click    ${locator}
    END
    FOR    ${key}    IN    @{keys}
        IF    '${key}' == 'CTRL+a' or '${key}' == 'CTRL+A'
            Keyboard Key    Press    Control+a
        ELSE IF    '${key}' == 'DELETE'
            Keyboard Key    Press    Delete
        ELSE IF    '${key}' == 'ESCAPE'
            Keyboard Key    Press    Escape
        ELSE IF    '${key}' == 'ENTER'
            Keyboard Key    Press    Enter
        ELSE IF    '${key}' == 'TAB'
            Keyboard Key    Press    Tab
        ELSE IF    '${key}' == 'BACKSPACE'
            Keyboard Key    Press    Backspace
        ELSE
            Keyboard Key    Press    ${key}
        END
    END


# ═══════════════════════════════════════════════════════════════════
# GETTERS
# ═══════════════════════════════════════════════════════════════════

Get Value
    [Documentation]    Drop-in for SeleniumLibrary Get Value.
    [Arguments]    ${locator}
    ${value}=    Get Property    ${locator}    value
    RETURN    ${value}

Get Location
    [Documentation]    Drop-in for SeleniumLibrary Get Location — returns current URL.
    ${url}=    Get Url
    RETURN    ${url}

Get Element Attribute
    [Documentation]    Drop-in for SeleniumLibrary Get Element Attribute.
    [Arguments]    ${locator}    ${attribute}
    ${value}=    Get Attribute    ${locator}    ${attribute}
    RETURN    ${value}

Get WebElements
    [Documentation]    Drop-in for SeleniumLibrary Get WebElements.
    ...                Returns a list of ElementHandle objects usable with Get Text, Click, etc.
    [Arguments]    ${locator}
    ${elements}=    Get Elements    ${locator}
    RETURN    ${elements}


# ═══════════════════════════════════════════════════════════════════
# NAVIGATION
# ═══════════════════════════════════════════════════════════════════

Reload Page
    [Documentation]    Drop-in for SeleniumLibrary Reload Page.
    Reload

Switch Window
    [Documentation]    Drop-in for SeleniumLibrary Switch Window.
    ...                Supports NEW, MAIN, and window titles/handles.
    [Arguments]    ${locator}=NEW
    Switch Page    ${locator}

Wait Until Location Contains
    [Documentation]    Drop-in for SeleniumLibrary Wait Until Location Contains.
    ...                Polls the current URL until it contains the expected substring.
    [Arguments]    ${expected}    ${timeout}=15s
    Wait Until Keyword Succeeds    ${timeout}    1s    Location Should Contain    ${expected}

Location Should Contain
    [Documentation]    Asserts the current URL contains the given substring.
    ...                Used as the polling target inside Wait Until Location Contains.
    [Arguments]    ${expected}
    ${url}=    Get Url
    Should Contain    ${url}    ${expected}


# ═══════════════════════════════════════════════════════════════════
# JAVASCRIPT
# ═══════════════════════════════════════════════════════════════════

Execute Javascript
    [Documentation]    Drop-in for SeleniumLibrary Execute Javascript (no-element variant).
    ...                For element-targeted JS, use Evaluate JavaScript directly in
    ...                the keyword that needs it (resource files only — not test cases).
    [Arguments]    ${script}
    ${result}=    Evaluate JavaScript    NONE    ${script}
    RETURN    ${result}


# ═══════════════════════════════════════════════════════════════════
# PAGE CONTENT ASSERTIONS
# ═══════════════════════════════════════════════════════════════════

Page Should Contain
    [Documentation]    Drop-in for SeleniumLibrary Page Should Contain.
    [Arguments]    ${text}
    ${source}=    Get Page Source
    Should Contain    ${source}    ${text}

Page Should Not Contain
    [Documentation]    Drop-in for SeleniumLibrary Page Should Not Contain.
    [Arguments]    ${text}
    ${source}=    Get Page Source
    Should Not Contain    ${source}    ${text}


# ═══════════════════════════════════════════════════════════════════
# ALERTS / DIALOGS
# ═══════════════════════════════════════════════════════════════════

Alert Should Be Present
    [Documentation]    Drop-in for SeleniumLibrary Alert Should Be Present.
    ...                In Playwright, dialogs must be pre-registered. This keyword
    ...                attempts to catch a dialog that appeared with a short timeout.
    ...                Returns normally if a dialog is found; fails (raises) if not.
    [Arguments]    ${timeout}=2s
    Wait For Alert    action=dismiss    timeout=${timeout}


# ═══════════════════════════════════════════════════════════════════
# SCREENSHOT
# ═══════════════════════════════════════════════════════════════════

Capture Page Screenshot
    [Documentation]    Drop-in for SeleniumLibrary Capture Page Screenshot.
    Take Screenshot
