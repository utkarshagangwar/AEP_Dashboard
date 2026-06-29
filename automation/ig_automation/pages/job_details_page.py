class JobDetailsPage:

    JOB_DETAILS_PATH = "dashboard/jobs"

    # =========================
    # Header / Breadcrumb
    # =========================
    # "Jobs" breadcrumb link — targets the <a href="/dashboard/jobs"> in the breadcrumb nav
    # Resilient: anchored to href, not visible text (text can be translated/changed)
    JOB_TITLE = "xpath=//a[@href='/dashboard/jobs']"

    # =========================
    # Sidebar Navigation
    # =========================
    DASHBOARD_TAB = "xpath=//aside//a[@href='/dashboard']"
    JOBS_TAB      = "xpath=//aside//a[@href='/dashboard/jobs']"
    SETTINGS_TAB  = "xpath=//aside//a[@href='/dashboard/settings']"
    # NOTE: No /dashboard/candidates route found in DOM — removed CANDIDATES_TAB.
    # Add back if the route is introduced.

    # =========================
    # Global Search (Top Header)
    # =========================
    # Anchored to placeholder — stable as long as UX copy doesn't change
    GLOBAL_SEARCH_INPUT = "xpath=//header//input[@placeholder='Search for jobs, templates etc...']"

    # =========================
    # Job Cards (Jobs listing page)
    # DOM: cards are <a href="/dashboard/{groupId}"> inside <main>
    # Pattern: href starts with /dashboard/ followed by a numeric segment (no sub-path)
    # This avoids matching sidebar nav links that also start with /dashboard/
    # =========================
    JOB_CARDS = "xpath=//main//a[matches(@href, '^/dashboard/[0-9]+$')]"

    # Fallback if the RF XPath engine doesn't support matches() (XPath 1.0):
    # Scopes to <main> to exclude sidebar links
    JOB_CARDS_V1 = "xpath=//main//a[contains(@href,'/dashboard/') and not(contains(@href,'/dashboard/jobs')) and not(contains(@href,'/dashboard/settings')) and not(contains(@href,'/dashboard/candidates'))]"

    FIRST_JOB_CARD = ("xpath=(//a[.//p[normalize-space(text())='Candidates']])[1]") or (
        "css=.infinite-scroll-component > div > div:first-child a")

    # Relative locators — used with a parent element reference
    JOB_CARD_TITLE     = "xpath=.//h3//span[contains(@class,'font-bold')]"
    JOB_TYPE_BADGE     = "xpath=.//span[contains(text(),'Full Time')]"
    JOB_CREATED_DATE   = "xpath=.//p[contains(text(),'Created')]"

    # =========================
    # Card Metrics (relative — call from a card element context)
    # =========================
    CANDIDATES_COUNT            = "xpath=.//p[normalize-space(text())='Candidates']/preceding-sibling::p"
    EVALUATION_SENT_COUNT       = "xpath=.//p[normalize-space(text())='Evaluation Sent']/preceding-sibling::p"
    EVALUATION_COMPLETED_COUNT  = "xpath=.//p[normalize-space(text())='Evaluation Completed']/preceding-sibling::p"

    # =========================
    # Card Actions (3-dot menu)
    # =========================
    CARD_MENU_BUTTON = "xpath=.//button[.//svg]"

    # =========================
    # Job Details Page — Candidate Search input
    # DOM: <input placeholder="Candidate name" ...>
    # =========================
    SEARCH_INPUT = "xpath=//input[@placeholder='Candidate name']"

    # =========================
    # Filter Button
    # DOM: <button ...><span ...>Filter by</span><svg .../></button>
    # Using normalize-space() to guard against extra whitespace
    # =========================
    FILTER_BUTTON = "xpath=//button[.//span[normalize-space()='Filter by'] or normalize-space()='Filter by']"

    APPLY_FILTER_BUTTON     = "xpath=//button[normalize-space()='Apply Filters' or .//*[normalize-space()='Apply Filters']]"
    STATUS_FILTER_OPTIONS   = "xpath=(//*[normalize-space()='Status']/following::button[@role='checkbox'])[1]"
    DEADLINE_FILTER_OPTIONS = "xpath=(//*[normalize-space()='Deadline']/following::button[@role='checkbox'])[1]"
    SCORE_FILTER_OPTIONS    = "xpath=(//*[normalize-space()='Score']/following::button[@role='checkbox'])[1]"
    MIN_SCORE_INPUT         = "xpath=(//input[contains(@placeholder,'Min') or @name='min' or @aria-label='Min score'])[1]"
    MAX_SCORE_INPUT         = "xpath=(//input[contains(@placeholder,'Max') or @name='max' or @aria-label='Max score'])[1]"

    # =========================
    # Candidate Count Label (mobile card view)
    # DOM: <h1 class="text-sm font-medium tracking-[-0.01em] text-[#152137]">1 Candidates</h1>
    # Anchored to structural tag + text pattern, not fragile Tailwind classes
    # =========================
    CANDIDATE_COUNT_LABEL = "xpath=//h1[contains(text(),'Candidate')]"

    # =========================
    # Candidates Table
    # =========================
    TABLE_ROWS = "xpath=//tbody/tr"

    # Candidate name: DOM puts name in a <p> inside the first <td>
    # (td contains: checkbox button + div > p.name + p.date-added)
    FIRST_CANDIDATE_NAME = "xpath=(//tbody/tr/td[1]//p[contains(@class,'sub-13px-md') or contains(@class,'font-semibold')])[1]"

    # =========================
    # Row Checkboxes
    # DOM: <button type="button" role="checkbox" ...>
    # =========================
    SELECT_ALL_CHECKBOX = "xpath=//thead//button[@role='checkbox']"
    ROW_CHECKBOX        = "xpath=//tbody//button[@role='checkbox']"
    FIRST_ROW_CHECKBOX  = "xpath=(//tbody//button[@role='checkbox'])[1]"
    SECOND_ROW_CHECKBOX = "xpath=(//tbody//button[@role='checkbox'])[2]"

    # =========================
    # Minutes Left Pill
    # DOM: <div class="bg-[#E58116] ...">...<span>589541</span><span>minutes left</span></div>
    # FIXED: Tailwind arbitrary-value classes with brackets are NOT valid in XPath.
    # Target by the unique text content instead.
    # =========================
    MINUTES_LEFT_PILL = "xpath=//*[.//span[normalize-space()='minutes left']]"

    # =========================
    # Download Reports Button
    # DOM: <span class="text-sm font-medium text-neutral-400">Download Reports</span>
    # FIXED: was 'Download reports' (lowercase r) — actual DOM has capital R
    # =========================
    DOWNLOAD_REPORTS_BUTTON = "xpath=//button[.//*[normalize-space()='Download Reports']]"

    # =========================
    # Add Candidate Buttons
    # NOTE: These buttons were not found in the job details page outerHTML provided.
    # They may appear after a dropdown click or on a different route.
    # Locators below are best-effort — validate against actual DOM when visible.
    # =========================
    NEW_CANDIDATE_BUTTON    = "xpath=//button[normalize-space()='New Candidate' or .//*[normalize-space()='New Candidate']]"
    IMPORT_FROM_EXCEL_BUTTON = "xpath=//button[normalize-space()='Import from Excel' or .//*[normalize-space()='Import from Excel']]"
    ADD_CANDIDATE_DROPDOWN  = "xpath=(//*[.//span[normalize-space()='minutes left']]/following::button)[1]"
    ADD_NEW_CANDIDATE_OPTION = NEW_CANDIDATE_BUTTON
    ADD_FROM_EXCEL_OPTION   = IMPORT_FROM_EXCEL_BUTTON

    # =========================
    # Round Tabs
    # DOM: <button class="rounded-lg gap-2 ... font-bold ...">Round 1 - Interview</button>
    # inside a flex container in the header section.
    # Anchored to the structural container, not fragile class names.
    # =========================
    ROUND_TABS      = "xpath=//header//button[.//p[contains(text(),'Round')]]"
    FIRST_ROUND_TAB = "xpath=(//header//button[.//p[contains(text(),'Round')]])[1]"

    # Add Round button (the "+ Add round" link)
    ADD_ROUND_BUTTON = "xpath=//button[.//*[contains(text(),'Add round')]]"

    # =========================
    # Send Interview Button
    # DOM: <button ...><p ...> Send\n                        interview</p></button>
    # FIXED: text is split across whitespace inside a <p> child — normalize-space on the <p>
    # The button itself also has a disabled="" attribute when no candidate is selected.
    # =========================
    SEND_INTERVIEW_BUTTON   = "xpath=//button[not(@disabled) and .//*[contains(normalize-space(),'Send') and contains(normalize-space(),'interview')]]"
    SEND_INTERVIEW_DISABLED = "xpath=//button[@disabled and .//*[contains(normalize-space(),'Send') and contains(normalize-space(),'interview')]]"
    SEND_INTERVIEW_ENABLED  = "xpath=//button[not(@disabled) and .//*[contains(normalize-space(),'Send') and contains(normalize-space(),'interview')]]"

    # Edit Interview button (separate from Send Interview)
    EDIT_INTERVIEW_BUTTON = "xpath=//button[.//*[normalize-space()='Edit interview']]"

    # =========================
    # Status Badges (in table rows)
    # Using row-agnostic locators — pass a row index from the keyword if needed.
    # Row-specific aliases kept for backward compatibility.
    # =========================
    STATUS_BADGE_IN_ROW_1 = "xpath=//tbody/tr[1]//div[contains(@class,'rounded-2xl')]//p"

    # Specific status matchers (row-1 convenience aliases)
    STATUS_SENT     = "xpath=//tbody/tr[1]//p[normalize-space()='Sent']"
    STATUS_NOT_SENT = "xpath=//tbody/tr[1]//p[normalize-space()='Not Sent']"
    STATUS_FINISHED = "xpath=//tbody/tr[1]//p[normalize-space()='Finished']"

    # Generic: find a status badge by text anywhere in the table
    STATUS_BADGE_BY_TEXT = "xpath=//tbody//p[normalize-space()='{status}']"  # use .format(status=...)

    # =========================
    # Toast Notifications
    # FIXED: XPath union operator (|) is not reliably supported inside a SeleniumLibrary
    # locator string. Replaced with a single XPath using 'or' predicates.
    # =========================
    SUCCESS_TOAST = "xpath=//*[@role='status' or contains(@class,'toast')][contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'success') or contains(.,'successfully')]"

    ERROR_TOAST = "xpath=//*[@role='status' or contains(@class,'toast')][contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'error') or contains(.,'failed')]"

    # =========================
    # Logout Button
    # DOM: <span class="... whitespace-nowrap">Logout</span> inside a <button>
    # FIXED: was text()='Logout' which fails on whitespace; normalize-space() is safe
    # =========================
    LOGOUT_BUTTON = "xpath=//button[.//span[normalize-space()='Logout']]"
