# ============================================================
# Candidates Page — Page Object Variables
# InterviewGod Pre-Prod  |  /dashboard/candidates
# Locator priority: normalize-space XPath > placeholder > role
# ============================================================

# --- Page Header ---
PAGE_HEADING = "xpath=//h1[normalize-space()='Candidates']"

# --- Header Action Buttons ---
# NOTE: "Import from CV" may be hidden depending on workspace plan.
# Use IMPORT_FROM_CV_BTN only after verifying it's visible.
IMPORT_FROM_CV_BTN      = "xpath=//button[normalize-space()='Import from CV']"
IMPORT_FROM_EXCEL_BTN   = "xpath=//button[normalize-space()='Import from Excel']"
ADD_CANDIDATES_BTN      = "xpath=//button[normalize-space()='Add Candidates' or normalize-space()='+ Add Candidates']"

# --- Filter / Search Row ---
SEARCH_INPUT            = "xpath=//input[@placeholder='Search candidate name']"
ALL_JOBS_DROPDOWN       = "xpath=//button[contains(@class,' ') and .//text()[normalize-space()='All Jobs']] | //button[normalize-space(string(.))='All Jobs'] | //button[normalize-space()='All Jobs']"
# Simpler fallback (used in keywords):
ALL_JOBS_DROPDOWN_BTN   = "xpath=//button[.//span[normalize-space()='All Jobs'] or normalize-space()='All Jobs']"
SORT_BY_BTN             = "xpath=//button[normalize-space()='Sort by']"
FILTER_BY_BTN           = "xpath=//button[normalize-space()='Filter by']"
SELECT_ALL_BTN          = "xpath=//button[contains(@class,'select-all') or (@aria-label and contains(@aria-label,'select')) or (count(.//*[name()='svg'])=1 and count(.//text()[normalize-space()])=0)]"

# --- Candidate Count Label ---
CANDIDATE_COUNT_LABEL   = "xpath=//span[contains(@class,'') and contains(normalize-space(),'Candidates') and matches(normalize-space(),'^[0-9]')]"
# Safer version without regex (for SeleniumLibrary):
CANDIDATE_COUNT_SAFE    = "xpath=//*[contains(normalize-space(),' Candidates') and not(self::body) and not(self::html) and (contains(@class,'count') or ancestor::*[contains(@class,'header') or contains(@class,'toolbar') or contains(@class,'filter')])]"
# Most reliable for the actual markup seen on the page:
CANDIDATE_COUNT_TEXT    = "xpath=//p[contains(normalize-space(),'Candidates')] | //span[contains(normalize-space(),'Candidates') and string-length(normalize-space()) < 30]"

# --- Candidate Cards ---
CANDIDATE_CARD          = "xpath=//div[contains(@class,'candidate-card') or (contains(@class,'card') and .//text()[normalize-space()='Job'])]"
CANDIDATE_FIRST_CARD    = "xpath=(//div[contains(@class,'candidate-card') or (contains(@class,'card') and .//text()[normalize-space()='Job'])])[1]"
CANDIDATE_CHECKBOX      = "xpath=(//input[@type='checkbox'])[2]"   # index [1] is select-all
VIEW_DETAILS_FIRST      = "xpath=(//button[normalize-space()='View Details'])[1]"
VIEW_DETAILS_BTN        = "xpath=//button[normalize-space()='View Details']"
THREE_DOT_MENU_FIRST    = "xpath=(//button[@aria-label='Options' or @aria-label='More options' or (count(.//*[name()='circle'])=3)])[1]"

# --- Loading States ---
LOADING_SPINNER         = "xpath=//*[contains(normalize-space(),'Loading candidates') or contains(@class,'skeleton') or contains(@class,'spinner') or contains(@class,'loading')]"
LOADING_CANDIDATES_TEXT = "xpath=//*[normalize-space()='Loading candidates...']"

# --- Empty State ---
EMPTY_STATE             = "xpath=//*[contains(normalize-space(),'No candidates') or contains(normalize-space(),'0 Candidates') or contains(@class,'empty')]"

# --- All Jobs Dropdown List ---
ALL_JOBS_OPTION         = "xpath=//div[contains(@class,'dropdown') or contains(@class,'menu') or @role='listbox' or @role='menu']//span[normalize-space()='All Jobs'] | //*[@role='option' and normalize-space()='All Jobs']"
JOB_OPTION              = "xpath=//*[@role='option' and contains(normalize-space(),'{job_name}')] | //div[contains(@class,'option') and contains(normalize-space(),'{job_name}')]"

# --- Sort Panel ---
SORT_NAME_ASCENDING     = "xpath=//label[contains(normalize-space(),'Ascending') and contains(normalize-space(),'A') and contains(normalize-space(),'Z')]"
SORT_NAME_DESCENDING    = "xpath=//label[contains(normalize-space(),'Descending') and contains(normalize-space(),'Z') and contains(normalize-space(),'A')]"
SORT_SCORE_HIGHEST      = "xpath=//label[contains(normalize-space(),'Highest')]"
SORT_SCORE_LOWEST       = "xpath=//label[contains(normalize-space(),'Lowest')]"
SORT_APPLY_BTN          = "xpath=//button[normalize-space()='Apply']"
SORT_CLEAR_BTN          = "xpath=//button[normalize-space()='Clear All']"

# --- Filter Panel ---
FILTER_STATUS_NOT_SENT  = "xpath=//input[@type='checkbox' and following-sibling::*[normalize-space()='Not sent'] or ..//*[normalize-space()='Not sent']]"
FILTER_STATUS_SENT      = "xpath=//input[@type='checkbox'][following-sibling::*[normalize-space()='Sent'] or ../label[normalize-space()='Sent']]"
FILTER_STATUS_FINISHED  = "xpath=//input[@type='checkbox'][following-sibling::*[normalize-space()='Finished'] or ../label[normalize-space()='Finished']]"
FILTER_APPLY_BTN        = "xpath=//button[normalize-space()='Apply filters']"
FILTER_CLEAR_BTN        = "xpath=//button[normalize-space()='Clear All']"

# Score range radio buttons
FILTER_SCORE_BELOW_50   = "xpath=//input[@type='radio'][following-sibling::*[normalize-space()='Below 50'] or ../label[normalize-space()='Below 50']]"
FILTER_SCORE_50_60      = "xpath=//input[@type='radio'][following-sibling::*[normalize-space()='50 - 60'] or ../label[normalize-space()='50 - 60']]"
FILTER_SCORE_ABOVE_90   = "xpath=//input[@type='radio'][following-sibling::*[normalize-space()='Above 90'] or ../label[normalize-space()='Above 90']]"
FILTER_SCORE_MIN        = "xpath=//input[@placeholder='Min']"
FILTER_SCORE_MAX        = "xpath=//input[@placeholder='Max']"

# --- Add Candidate Modal ---
ADD_MODAL_TITLE         = "xpath=//h2[normalize-space()='Add candidate']"
ADD_MODAL_NAME          = "xpath=//input[@placeholder='Full name']"
ADD_MODAL_EMAIL         = "xpath=//input[@placeholder='name@gmail' or @type='email']"
ADD_MODAL_PHONE         = "xpath=//input[@placeholder='Candidate Phone Number']"
ADD_MODAL_SUBMIT_BTN    = "xpath=//button[normalize-space()='Add candidate']"
ADD_MODAL_CLOSE_BTN     = "xpath=//button[@aria-label='Close'] | //button[contains(@class,'close')]"

# --- Import CV Modal ---
IMPORT_CV_MODAL_TITLE   = "xpath=//h2[contains(normalize-space(),'Import Candidate from CV')]"
IMPORT_CV_BROWSE_BTN    = "xpath=//button[normalize-space()='Browse Files']"
IMPORT_CV_CANCEL_BTN    = "xpath=//button[normalize-space()='Cancel']"

# --- Import Excel Modal ---
IMPORT_EXCEL_MODAL_TITLE = "xpath=//h2[contains(normalize-space(),'Import Excel File')]"
IMPORT_EXCEL_BROWSE_BTN  = "xpath=//button[normalize-space()='Browse Files']"
IMPORT_EXCEL_IMPORT_BTN  = "xpath=//button[normalize-space()='Import Candidates']"
IMPORT_EXCEL_CANCEL_BTN  = "xpath=//button[normalize-space()='Cancel']"

# --- View Details Modal ---
VIEW_DETAILS_MODAL_TITLE   = "xpath=//h2[contains(normalize-space(),'Candidate Evaluation Details')]"
VIEW_DETAILS_CANDIDATE_NAME = "xpath=//h3[normalize-space()='Candidate Name']/following-sibling::p"
VIEW_DETAILS_EMAIL          = "xpath=//h3[normalize-space()='Email ID']/following-sibling::p"
VIEW_DETAILS_PHONE          = "xpath=//h3[normalize-space()='Phone Number']/following-sibling::p"
VIEW_DETAILS_VIEW_REPORT    = "xpath=//button[normalize-space()='View Report']"
VIEW_DETAILS_CLOSE_BTN      = "xpath=//button[@aria-label='Close']"

# --- Three-Dot Menu Options ---
MENU_EDIT_DETAILS           = "xpath=//div[normalize-space()='Edit Details']"
MENU_SEND_AI_CALL           = "xpath=//div[normalize-space()='Send AI Call Screening']"
MENU_SEND_AI_ASSESSMENT     = "xpath=//div[normalize-space()='Send AI Assessment']"
MENU_SEND_AI_INTERVIEW      = "xpath=//div[normalize-space()='Send AI Interview']"
MENU_ADD_MULTIPLE_JOBS      = "xpath=//div[normalize-space()='Add to Multiple Jobs']"
MENU_REMOVE_CANDIDATES      = "xpath=//div[normalize-space()='Remove candidates']"