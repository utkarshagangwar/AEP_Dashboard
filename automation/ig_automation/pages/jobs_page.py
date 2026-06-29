class JobsPage:

    # =========================
    # Navigation — Sidebar
    # Scoped to the sidebar anchor to avoid matching breadcrumb spans
    # =========================
    JOBS_MENU = "xpath=//a[@href='/dashboard/jobs']//span[contains(text(),'Jobs')]"

    # =========================
    # Page Title
    # FIX (previous round): The title is a <p> tag, NOT an <h1>
    # =========================
    PAGE_TITLE = "xpath=//p[normalize-space(text())='Jobs' and contains(@class,'font-bold')]"

    # =========================
    # Add Job Button
    # The <button> wraps an <a> tag; clicking the anchor directly is most reliable
    # =========================
    ADD_JOB_BTN = "xpath=//a[@href='/dashboard/jobs/create_job']"

    # =========================
    # Metrics Cards
    # FIX: Labels live inside nested <div>/<p> elements; the numeric value is in an
    #      <h2> with a blue colour class. Pattern confirmed from dashboard_page.py.
    #      For simple visibility checks use the *_LABEL locators below.
    # NOTE: Actual label is 'Evaluations Scheduled', NOT 'Interview Scheduled'
    # =========================
    TOTAL_JOBS          = "xpath=//p[normalize-space()='Total Jobs']/following::h2[1]"
    TOTAL_CANDIDATES    = "xpath=//div[.//*[normalize-space()='Total Candidates']]//h2[contains(@class,'text-[#1B69F8]') or contains(@class,'text-blue')]"
    INTERVIEW_SCHEDULED = "xpath=//div[.//*[normalize-space()='Evaluations Scheduled']]//h2[contains(@class,'text-[#1B69F8]') or contains(@class,'text-blue')]"
    INTERVIEW_COMPLETED = "xpath=//div[.//*[normalize-space()='Interview Completed']]//h2[contains(@class,'text-[#1B69F8]') or contains(@class,'text-blue')]"

    # Label-only locators for visibility assertions
    TOTAL_JOBS_LABEL            = "xpath=//p[normalize-space()='Total Jobs']"
    # FIX: Dual-render UI (Tailwind) — //* matched both the wrapper <div> and the <p> tag (strict mode violation).
    # Scope to <p> only and exclude the mobile lg:hidden copy.
    TOTAL_CANDIDATES_LABEL      = ("xpath=//p[normalize-space()='Total Candidates'"
                                   " and not(ancestor::div[contains(@class,'lg:hidden')])]")
    EVALUATIONS_SCHEDULED_LABEL = "xpath=//*[normalize-space()='Evaluations Scheduled']"
    INTERVIEW_COMPLETED_LABEL   = "xpath=//*[normalize-space()='Interview Completed']"

    # =========================
    # Search & Filters
    # =========================
    SEARCH_INPUT = "xpath=//input[@placeholder='Search Jobs']"
    FILTER_BTN   = "xpath=//button[.//span[normalize-space(text())='Filter by']]"
    SORT_BTN     = "xpath=//button[.//span[normalize-space(text())='Sort by']]"

    # =========================
    # Job Cards
    # Cards have NO 'card' CSS class — reliable anchor is the by-group href
    # =========================
    JOB_CARD       = "xpath=//a[contains(@href,'/dashboard/by-group/')]"
    FIRST_JOB_CARD = "xpath=(//a[contains(@href,'/dashboard/by-group/')])[1]"

    # Job title span inside a card (relative usage)
    JOB_TITLE = "xpath=.//h3//span[contains(@class,'font-bold')]"

    # 3-dot kebab menu button
    JOB_MENU  = "xpath=(//button[.//*[name()='svg'][.//*[name()='circle'][@r='1']]])[1]"

    # =========================
    # Create Job Page
    # Confirmed from screenshot: heading is rendered as a plain element (h1/p)
    # =========================
    CREATE_JOB_HEADER = "xpath=//*[self::h1 or self::h2 or self::p][contains(normalize-space(.),'Create Job')]"

    # =========================
    # Create Job Form Fields
    # Confirmed from screenshot: placeholder is exactly "Job" (not "Job Title")
    # =========================
    JOB_TITLE_INPUT = "xpath=//input[@placeholder='Job']"

    EXPERIENCE_INPUT = "xpath=//input[contains(@placeholder,'years') or @placeholder='eg. 2-4 years']"

    # Employment Type dropdown trigger
    # FIX: Trigger is a <button> containing "Select Type" placeholder text
    EMPLOYMENT_DROPDOWN = "xpath=//button[contains(normalize-space(.),'Select Type')]"

    # Option items inside the open dropdown panel
    # FIX: Options are <button> tags (confirmed from DevTools screenshot), NOT <div> tags
    EMPLOYMENT_OPTIONS = "xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button"
    FULL_TIME_OPTION   = "xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Full Time')]"
    PART_TIME_OPTION   = "xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Part Time')]"
    FREELANCING_OPTION = "xpath=//div[contains(@class,'absolute') and contains(@class,'z-20')]//button[contains(normalize-space(text()),'Freelancing')]"

    # =========================
    # AI Prompt Textarea
    # FIX: "AI Prompt" is a placeholder attribute, NOT visible text in the DOM.
    #      The old locator //*[contains(text(),'AI Prompt')] always failed because
    #      there is no element with that text content — only a placeholder.
    #      Target the textarea directly by its placeholder attribute.
    # =========================
    AI_PROMPT = "xpath=//textarea[@placeholder='AI Prompt']"

    JOB_DESCRIPTION = "xpath=//textarea[contains(@placeholder,'Paste job description') or contains(@placeholder,'job description')]"

    SAVE_BTN   = "xpath=//button[normalize-space(.)='Save']"
    CANCEL_BTN = "xpath=//button[normalize-space(.)='Cancel']"

    # =========================
    # Interview Templates
    # =========================
    TEMPLATE_SECTION       = "xpath=//*[self::h2 or self::h3 or self::p][contains(text(),'Interview Templates')]"
    AI_CALL_TEMPLATE       = "xpath=//*[contains(normalize-space(.),'AI Call Screening')]"
    AI_ASSESSMENT_TEMPLATE = "xpath=//*[contains(normalize-space(.),'AI Assessment')]"
    AI_INTERVIEW_TEMPLATE  = "xpath=//*[contains(normalize-space(.),'AI Interview')]"
    SELECT_TEMPLATE_BTN    = "xpath=//button[contains(normalize-space(.),'Select Template')]"
    CREATE_TEMPLATE_LINK   = "xpath=//*[contains(normalize-space(text()),'Create template')]"

    # =========================
    # Wizard — Step 1 (Method Selection)
    # URL: /dashboard/jobs/create_job — first load always lands here
    # =========================
    STEP1_HEADING    = "xpath=//h1[normalize-space()='Job Description']"

    # Method cards — target the <h3> heading inside each card button.
    # Using h3 text is more reliable than class-based matching and avoids any
    # Playwright issues with square-bracket Tailwind class names in XPath strings.
    GENERATE_AI_CARD = "xpath=//button[.//h3[normalize-space()='Generate with AI']]"
    PASTE_JD_CARD    = "xpath=//button[.//h3[normalize-space()='Paste Existing JD']]"

    # Footer nav — present on every wizard step
    WIZARD_CONTINUE_BTN = "xpath=//button[contains(normalize-space(),'Continue')]"
    WIZARD_BACK_BTN     = "xpath=//button[normalize-space()='Back' or normalize-space()='← Back']"

    # =========================
    # Wizard — Step 2 (Generate with AI)
    # Heading: "Generate Job Description"
    # =========================
    STEP2_AI_HEADING    = "xpath=//h1[normalize-space()='Generate Job Description']"

    # Role Details inputs — placeholders confirmed from live DOM inspection
    JOB_TITLE_AI_INPUT  = "xpath=//input[@placeholder='Senior Frontend Engineer']"
    EMP_TYPE_SELECT     = "xpath=//select[option[normalize-space()='Full-time']]"
    EXP_LEVEL_AI_INPUT  = "xpath=//input[@placeholder='2 years']"

    # Action buttons — "Generate JD" has a sparkle icon so use contains; exclude Regenerate variant
    GENERATE_JD_BTN     = ("xpath=//button[contains(normalize-space(),'Generate JD')"
                            " and not(contains(normalize-space(),'Regenerate'))"
                            " and not(contains(normalize-space(),'Generating'))]")
    GENERATING_BTN      = "xpath=//button[contains(normalize-space(),'Generating')]"
    REGENERATE_JD_BTN   = "xpath=//button[normalize-space()='Regenerate Entire JD']"

    # Generation Progress panel — each step label (use following-sibling for status)
    GEN_STEP_UNDERSTANDING = "xpath=//p[normalize-space()='Understanding role']"
    GEN_STEP_CREATING      = "xpath=//p[normalize-space()='Creating responsibilities']"
    GEN_STEP_EXTRACTING    = "xpath=//p[normalize-space()='Extracting skills']"
    GEN_STEP_FINALIZING    = "xpath=//p[normalize-space()='Finalizing JD']"

    # =========================
    # Wizard — Step 2 (Paste Existing JD)
    # Heading: "Paste Existing JD"
    # =========================
    STEP2_PASTE_HEADING   = "xpath=//h1[normalize-space()='Paste Existing JD']"

    # Role Details inputs for Paste path (different placeholder from AI path)
    JOB_TITLE_PASTE_INPUT = "xpath=//input[@placeholder='Senior Engineer']"

    # JD paste area — it is a contenteditable <div> with role="textbox", NOT a <textarea>.
    # aria-label is the most stable unique attribute on this element.
    JD_PASTE_TEXTAREA     = ("xpath=//div[@contenteditable='true'"
                              " and @aria-label='Existing job description']")

    UPLOAD_DOC_BTN        = "xpath=//button[normalize-space()='Upload .docx / .pdf']"

    # Analysis panel steps (same structural pattern as Generation Progress)
    ANALYSIS_JD_DETECTED  = "xpath=//p[normalize-space()='JD Detected']"
    ANALYSIS_SECTIONS     = "xpath=//p[normalize-space()='Sections parsed']"
    ANALYSIS_EXTRACTING   = "xpath=//p[normalize-space()='Extracting skills']"
    ANALYSIS_READY        = "xpath=//p[normalize-space()='Ready to review']"

    # =========================
    # Wizard — Step 3 (Review & Refine Skill Map)
    # Heading: "Review & Refine Skill Map"
    # =========================
    STEP3_HEADING         = "xpath=//h1[normalize-space()='Review & Refine Skill Map']"

    TAB_TOP_SKILLS        = "xpath=//button[normalize-space()='Top Skills (Must Have)']"
    TAB_SECONDARY_SKILLS  = "xpath=//button[normalize-space()='Secondary Skills (Important)']"
    TAB_TRAINABLE_SKILLS  = "xpath=//button[normalize-space()='Trainable Skills (Nice to Have)']"
    ADD_OWN_SKILL_BTN     = "xpath=//button[contains(normalize-space(),'Add Your Own Skill')]"
    CONFIRM_CREATE_BTN    = ("xpath=//button[contains(normalize-space(),'Confirm')"
                             " and contains(normalize-space(),'Create')]")