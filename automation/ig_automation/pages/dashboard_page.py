class DashboardPage:

    # ── Navigation (Sidebar) ────────────────────────────────────────────────
    # §7.4 dual-render fix: this app renders the full nav twice — once inside
    # `lg:hidden` (mobile hamburger, hidden at 1280px) and once inside
    # `hidden lg:flex` (desktop sidebar, visible at 1280px).
    # Without the exclusion both spans match; the mobile copy is CSS-hidden,
    # so locator.wait_for(visible) times out even though the desktop copy is
    # visible. Scoping to NOT(lg:hidden) leaves exactly 1 element.
    DASHBOARD_MENU  = ("xpath=//span[normalize-space()='Dashboard'"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    JOBS_MENU       = ("xpath=//span[normalize-space()='Jobs'"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    CANDIDATES_MENU = ("xpath=//span[normalize-space()='Candidates'"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    TEMPLATES_MENU  = ("xpath=//span[normalize-space()='Templates'"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    SETTINGS_MENU   = ("xpath=//span[normalize-space()='Settings'"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    LOGOUT_BTN      = ("xpath=//button[contains(.,'Logout')"
                       " and not(ancestor::div[contains(@class,'lg:hidden')])]")

    # ── Page Header ─────────────────────────────────────────────────────────
    # FIX: removed class condition – the h1 with text 'Overview' is unique enough
    PAGE_TITLE = "xpath=//h1[normalize-space()='Overview']"

    # ── Credit Balance ───────────────────────────────────────────────────────
    # The UI renders the orange pill TWICE: once in a `lg:hidden` div (mobile)
    # and once in `hidden lg:flex` (desktop). At 1920px, only the desktop copy
    # is visible. Scoping to the non-lg:hidden ancestor keeps both locators to
    # exactly 1 element and avoids Playwright strict-mode violations.
    CREDIT_BALANCE       = ("xpath=//div[contains(@class,'rounded-3xl')"
                            " and .//span[normalize-space()='Credit Balance:']"
                            " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    CREDIT_BALANCE_VALUE = ("xpath=//span[normalize-space()='Credit Balance:'"
                            " and not(ancestor::div[contains(@class,'lg:hidden')])]"
                            "/following-sibling::span")

    # ── Metric Cards – numeric value cells ──────────────────────────────────
    # HTML structure of each card (condensed):
    #   <div class="... gap-3">                     ← inner-flex column
    #     <div class="... justify-between">          ← label row
    #       <div class="font-bold">Total Candidates</div>   ← label
    #     </div>
    #     <div class="... gap-2">                   ← following-sibling of label row
    #       <div class="text-blue-600 text-2xl">1333</div>  ← VALUE  ← target
    #     </div>
    #   </div>
    #
    # Sibling navigation from the label div resolves to exactly 1 element.
    # The old ancestor-descendant pattern matched 4 nesting levels each.
    TOTAL_CANDIDATES = ("xpath=//div[normalize-space()='Total Candidates']"
                        "/parent::div/following-sibling::div"
                        "/div[contains(@class,'text-blue-600')]")
    TOTAL_JOBS       = ("xpath=//div[normalize-space()='Jobs']"
                        "/parent::div/following-sibling::div"
                        "/div[contains(@class,'text-blue-600')]")
    IN_PROGRESS      = ("xpath=//div[normalize-space()='Interview In Progress']"
                        "/parent::div/following-sibling::div"
                        "/div[contains(@class,'text-blue-600')]")
    COMPLETED        = ("xpath=//div[normalize-space()='Interview Completed']"
                        "/parent::div/following-sibling::div"
                        "/div[contains(@class,'text-blue-600')]")

    # ── Recent Activities ────────────────────────────────────────────────────
    # FIX: use normalize-space() so leading/trailing whitespace doesn't break match
    RECENT_ACTIVITIES_TITLE   = "xpath=//*[contains(text(),'Recent Activities')]"

    # FIX: scoped to the scrollable activities container; each activity card
    #      is a border-zinc-200 div directly inside the scroll wrapper
    RECENT_ACTIVITIES_SECTION = "xpath=//div[contains(@class,'dashboard-scroll')]"

    ACTIVITY_ITEMS = "xpath=//div[contains(@class,'dashboard-scroll')] //div[contains(@class,'border-zinc-200')]"

    # Individual activity sub-elements (for content validation)
    ACTIVITY_TITLE_CELLS = "xpath=//div[contains(@class,'dashboard-scroll')] //span[contains(@class,'text-slate-800')]"

    ACTIVITY_TIMESTAMP_CELLS = ("xpath=//div[contains(@class,'dashboard-scroll')] //span[contains(@class,"
                                "'whitespace-nowrap')]")

    # ── Loader (optional – check before asserting data) ──────────────────────
    LOADER = "xpath=//div[contains(@class,'loading')]"