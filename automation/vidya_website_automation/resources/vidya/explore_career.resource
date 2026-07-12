*** Settings ***
Resource    ../common/browser.resource
Resource    ../common/navigation.resource
Library     Browser
Library     String


*** Variables ***
# Page-level
${EXPLORE_CAREER_LOCATOR}          text=Explore Career Opportunities Browse jobs & internships from trusted companies,
${EXPLORE_CAREER_TAB_ALL}          text="All"
${EXPLORE_CAREER_TAB_INTERNSHIPS}     text="Internships"
${EXPLORE_CAREER_TAB_JOBS}          text="Jobs"
${EXPLORE_CAREER_JOBS_LIST_ITEMS}      xpath=//div[contains(@class,'rounded-xl') and contains(@class,'cursor-pointer')]
${EXPLORE_NO_INTERNSHIPS}    text="No internships found"

# Search and filter elements
${SEARCH_JOBS_INPUT}    role=textbox[name="Search by role, skill, or company..."]
${JOB_TYPE_DROPDWON}    xpath=//button[contains(normalize-space(.),'Job type') or contains(normalize-space(.),'Full-Time') or contains(normalize-space(.),'Part-Time') or contains(normalize-space(.),'Contract')]
${JOB_TYPE_BTN}=    xpath=//button[contains(.,'Job type')]
${FIELD_FULL_TIME}    css=li[data-value="full-time"]
${FIELD_PART_TIME}    css=li[data-value="part-time"]
${FIELD_CONTRACT}    css=li[data-value="contract"]
${WORK_MODE_DROPDOWN}    xpath=//button[contains(normalize-space(.),'Work mode') or contains(normalize-space(.),'Remote') or contains(normalize-space(.),'On Site') or contains(normalize-space(.),'Hybrid')]
${FIELD_REMOTE}    css=li[data-value="remote"]
${FIELD_ON_SITE}    css=li[data-value="onSite"]
${FIELD_HYBRID}    css=li[data-value="hybrid"]
${SORT_BY_NEWEST}    text="Newest"
${SORT_BY_HIGHEST}    text="Highest paid"
${SORT_BY_TRENDING}    text="Trending"
${TOTAL_RESOULTS_COUNT}    text="Results found"

# Job listing details
${JOB_POSTED_DATE}    xpath=//div[3]/p
${JOB_SALARY}    xpath=//p[contains(normalize-space(),'₹')]
${JOB_PART_TIME_LABEL}    xpath=//div[contains(@class,'capitalize')][contains(.,'Part-Time')]
${JOB_CONTRACT_LABEL}    xpath=//div[contains(@class,'capitalize')][contains(.,'Contract')]
${JOB_REMOTE_LABEL}     xpath=//div[contains(@class,'capitalize')][contains(.,'Remote')]
${JOB_ON_SITE_LABEL}    xpath=//div[contains(@class,'capitalize')][contains(.,'On Site')]
${JOB_HYBRID_LABEL}    xpath=//div[contains(@class,'capitalize')][contains(.,'Hybrid')]
${JOB_COMPANY_NAME}    css=.flex.h-\[45px\].w-\[45px\]
${JOB_TITLE}         css=div.font-inter.group.flex.cursor-pointer.flex-col.rounded-xl p.text-lg
${INTERNSHIP_LABEL}    text="Internship"
${JOB_LABEL}         css=div.font-inter.group.flex.cursor-pointer.flex-col.rounded-xl
${JOB_CARD}     xpath=//div[contains(@class,'cursor-pointer') and contains(@class,'rounded-xl')]
${JOB_DETAIL_PAGE}    xpath=//main[contains(@class,'mt-nav-height')]
${JOB_DETAIL_NOT_FOUND}    xpath=//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'job not found') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'not found') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'404')]
${JOB_FULL_TIME_LABEL}    xpath=//div[contains(@class,'capitalize')][contains(.,'Full-Time')]
${JOB_DETAIL_APPLY_BUTTON}    role=button[name="Apply"]
${JOB_DETAIL_SUBMIT_BUTTON}    role=button[name="Submit"]
${JOB_DETAIL_UPLOAD_BUTTON}    role=button[name="Upload File"]
${JOB_DETAIL_UPLOAD_INPUT}    css=input[type="file"]
${JOB_DETAIL_READY_TO_APPLY}    text="Ready to Apply?"
${JOB_DETAIL_ABOUT_JOB}    text="About the Job"
${JOB_DETAIL_UPLOAD_HELP_TEXT}    text="Supports PDF, DOC, DOCX (Max 10 MB)"
${JOB_DETAIL_UPLOAD_ERROR}    xpath=//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'unsupported') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'invalid') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'file type') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'file size') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'10 mb') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'resume')]
${SIMILAR_JOBS_HEADING}    text=Explore similar Internships
${SIMILAR_JOB_CARD}    xpath=//div[contains(@class,'cursor-pointer') and contains(@class,'rounded-xl')]
${SIMILAR_JOB_CARDS}    xpath=//div[contains(@class,'cursor-pointer') and contains(@class,'rounded-xl')]
${SIMILAR_JOB_FIRST_TITLE}    xpath=//div[contains(@class,'cursor-pointer') and contains(@class,'rounded-xl')][1]//p[contains(@class,'text-lg')]
${SIMILAR_JOB_FIRST_COMPANY}    xpath=//div[contains(@class,'cursor-pointer') and contains(@class,'rounded-xl')][1]//p[contains(@class,'text-base')]

# Pagination elements
${PAGINATION_NEXT_BUTTON}    role=button[name="Next"]
${PAGINATION_PREVIOUS_BUTTON}    role=button[name="Prev"]
${PAGINATION_PAGE_NUMBERS_1}    xpath=//span[normalize-space()='1 / 2']
${PAGINATION_PAGE_NUMBERS_2}    xpath=//span[normalize-space()='2 / 2']

# Test data
${TEST_JOB_TITLE_1}    text="Automation Tester"
${TEST_JOB_TITLE_2}    text="Business Analyst"
${TEST_JOB_TITLE_3}    text="Backend Developer"
${TEST_JOB_COMPANY_1}    text="Techno world"
${TEST_JOB_COMPANY_2}    text="GIANT"
${TEST_JOB_COMPANY_3}    text="Intellect Studio Plus"
${TEST_JOB_SKILL_1}    text="Sales"
${TEST_JOB_SKILL_2}    text="Balsamiq"
${TEST_JOB_SKILL_3}    text="jira"
${CAREER_RESUME_VALID_PDF}    test_data/resume_valid.pdf
${CAREER_RESUME_UNSUPPORTED}    test_data/resume_valid.jpg
${CAREER_RESUME_OVERSIZED}    test_data/resume_oversized_11mb.pdf


*** Keywords ***

*** Keywords ***

Open Explore Career Page
    Go To     ${BASE_URL}/jobs
    Wait For Elements State    ${EXPLORE_CAREER_TAB_ALL}    visible

Open First Job Detail Page
    Open Explore Career Page
    Wait For Elements State    ${JOB_CARD} >> nth=0    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${JOB_CARD} >> nth=0
    Click    ${JOB_CARD} >> nth=0
    Verify Job Detail Page Is Loaded

Get First Job Detail Url
    Open First Job Detail Page
    ${job_url}=    Get Url
    RETURN    ${job_url}

Verify Job Detail Page Is Loaded
    Wait For Elements State    ${JOB_DETAIL_PAGE}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${JOB_DETAIL_ABOUT_JOB}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${JOB_DETAIL_READY_TO_APPLY}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${JOB_DETAIL_UPLOAD_HELP_TEXT}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${JOB_DETAIL_SUBMIT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Upload Job Detail Resume
    [Arguments]    ${file_path}
    Wait For Elements State    ${JOB_DETAIL_UPLOAD_INPUT}    attached    timeout=${DEFAULT_TIMEOUT}
    Upload File By Selector    ${JOB_DETAIL_UPLOAD_INPUT}    ${file_path}
    ${selected_files}=    Get Property    ${JOB_DETAIL_UPLOAD_INPUT}    files
    ${selected_count}=    Get Length    ${selected_files}
    Should Be Equal As Integers    ${selected_count}    1    Job detail resume upload did not select a file.

Verify Job Detail Resume Upload Stability
    Wait For Elements State    ${JOB_DETAIL_UPLOAD_INPUT}    attached    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${JOB_DETAIL_SUBMIT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Verify Job Detail Upload Error Or Stable Form
    ${has_error}=    Run Keyword And Return Status    Wait For Elements State    ${JOB_DETAIL_UPLOAD_ERROR}    visible    timeout=5s
    IF    not ${has_error}
        Verify Job Detail Resume Upload Stability
    END

Submit Job Detail Application
    Wait For Elements State    ${JOB_DETAIL_SUBMIT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${JOB_DETAIL_SUBMIT_BUTTON}

Verify Job Detail Apply Without Resume
    Submit Job Detail Application
    Verify Job Detail Upload Error Or Stable Form

Verify Job Detail Apply After Resume Upload
    Upload Job Detail Resume    ${CAREER_RESUME_VALID_PDF}
    Submit Job Detail Application
    Wait For Load State    networkidle
    Verify Job Detail Resume Upload Stability

Verify Similar Job Cards Are Clickable
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    Wait For Elements State    ${SIMILAR_JOB_CARD} >> nth=0    visible    timeout=${DEFAULT_TIMEOUT}
    ${before_url}=    Get Url
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    ${after_url}=    Get Url
    Should Not Be Equal    ${before_url}    ${after_url}
    Verify Job Detail Page Is Loaded

Verify Multiple Similar Job Navigation
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    ${initial_url}=    Get Url
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded
    ${second_level_url}=    Get Url
    Should Not Be Equal    ${initial_url}    ${second_level_url}
    Wait For Elements State    ${SIMILAR_JOB_CARD} >> nth=0    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded

Verify Browser Back Navigation From Similar Job
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    ${previous_url}=    Get Url
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded
    Go Back
    Wait For Load State    networkidle
    ${current_url}=    Get Url
    Should Be Equal    ${previous_url}    ${current_url}
    Verify Job Detail Page Is Loaded

Verify No Similar Jobs Available State
    ${has_similar}=    Run Keyword And Return Status    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=5s
    IF    ${has_similar}
        Wait For Elements State    ${SIMILAR_JOB_CARD} >> nth=0    visible    timeout=${DEFAULT_TIMEOUT}
    ELSE
        ${page_text}=    Get Text    css=body
        ${page_text}=    Convert To Lower Case    ${page_text}
        ${has_no_similar}=    Run Keyword And Return Status    Should Contain    ${page_text}    no similar
        ${has_no_related}=    Run Keyword And Return Status    Should Contain    ${page_text}    no related
        ${has_no_jobs}=    Run Keyword And Return Status    Should Contain    ${page_text}    no jobs
        Should Be True    ${has_no_similar} or ${has_no_related} or ${has_no_jobs}    Empty similar-jobs state was not shown.
    END

Verify Similar Job Card Whole Area Clickable
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    ${before_url}=    Get Url
    Click    ${SIMILAR_JOB_FIRST_TITLE}
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded
    Go Back
    Wait For Load State    networkidle
    Click    ${SIMILAR_JOB_FIRST_COMPANY}
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded
    Go Back
    Wait For Load State    networkidle
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded

Verify Job Detail Refresh
    ${before_url}=    Get Url
    Reload
    Wait For Load State    networkidle
    ${after_url}=    Get Url
    Should Be Equal    ${before_url}    ${after_url}
    Verify Job Detail Page Is Loaded

Verify Direct Job Url Access
    [Arguments]    ${job_url}
    New Page    ${job_url}
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded

Verify Invalid Job Url Handling
    ${current_url}=    Get Url
    ${invalid_url}=    Replace String    ${current_url}    /jobs/    /jobs/invalid-
    Go To    ${invalid_url}
    Wait For Load State    networkidle
    ${has_not_found}=    Run Keyword And Return Status    Wait For Elements State    ${JOB_DETAIL_NOT_FOUND}    visible    timeout=5s
    Should Be True    ${has_not_found}    Invalid job URL did not show an error state.

Verify No Blank Page During Navigation
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    FOR    ${index}    IN RANGE    2
        Click    ${SIMILAR_JOB_CARD} >> nth=0
        Wait For Load State    networkidle
        Verify Job Detail Page Is Loaded
        Go Back
        Wait For Load State    networkidle
        Verify Job Detail Page Is Loaded
    END

Verify Tab Switching Works Correctly
    Open Explore Career Page
    Click    ${EXPLORE_CAREER_TAB_ALL}
    Verify Jobs Only
    Click    ${EXPLORE_CAREER_TAB_JOBS}
    Verify Jobs Only
    Click    ${EXPLORE_CAREER_TAB_INTERNSHIPS}
    Wait For Elements State    ${EXPLORE_NO_INTERNSHIPS}    visible
    Click    ${EXPLORE_CAREER_TAB_ALL}
    Verify Jobs Only

Search And Verify Partial Result
    [Arguments]    ${search_text}
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible
    Fill Text    ${SEARCH_JOBS_INPUT}    ${search_text}
    ${count}=    Get Element Count    ${JOB_LABEL}
    Should Be True    ${count} > 0    No matching jobs appeared for partial search: ${search_text}

Verify Similar Job Opens Correct Details
    Wait For Elements State    ${SIMILAR_JOBS_HEADING}    visible    timeout=${DEFAULT_TIMEOUT}
    Scroll To Element    ${SIMILAR_JOBS_HEADING}
    ${similar_title}=    Get Text    ${SIMILAR_JOB_FIRST_TITLE}
    ${similar_company}=    Get Text    ${SIMILAR_JOB_FIRST_COMPANY}
    ${similar_title}=    Replace String    ${similar_title}    ...    ${EMPTY}
    Click    ${SIMILAR_JOB_CARD} >> nth=0
    Wait For Load State    networkidle
    Verify Job Detail Page Is Loaded
    ${page_text}=    Get Text    css=body
    ${page_text}=    Convert To Lower Case    ${page_text}
    ${similar_title}=    Convert To Lower Case    ${similar_title}
    ${similar_company}=    Convert To Lower Case    ${similar_company}
    Should Contain    ${page_text}    ${similar_title}
    Should Contain    ${page_text}    ${similar_company}

Search And Verify Result
    [Arguments]    ${search_text}
    Fill Text    ${SEARCH_JOBS_INPUT}    ${search_text}          
    Wait For Elements State    text="${search_text}"    visible

Search And Verify Result Skills
    [Arguments]    ${search_text}

    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible
    Fill Text    ${SEARCH_JOBS_INPUT}    ${search_text}

    ${count}=    Get Element Count    ${JOB_LABEL}
    Log To Console    Card Count= ${count}
    Should Be True    ${count} > 0    No job cards were shown for skill search: ${search_text}

    ${page_text}=    Get Text    css=body
    ${page_text}=    Convert To Lower Case    ${page_text}
    ${search_text}=    Convert To Lower Case    ${search_text}
    Should Contain    ${page_text}    ${search_text}

    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible

Verify Search Results Are Case Insensitive
    [Arguments]    ${keyword}
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible

    ${upper_keyword}=    Convert To Upper Case    ${keyword}
    Fill Text    ${SEARCH_JOBS_INPUT}    ${upper_keyword}
    Wait For Load State    networkidle
    ${upper_results}=    Get Text    ${JOB_LABEL}

    Fill Text    ${SEARCH_JOBS_INPUT}    ${keyword}
    Wait For Load State    networkidle
    ${lower_results}=    Get Text    ${JOB_LABEL}

    Should Be Equal As Strings    ${upper_results}    ${lower_results}

Verify Special Character Search Handles Input
    [Arguments]    ${search_text}
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible
    Fill Text    ${SEARCH_JOBS_INPUT}    ${search_text}
    Wait For Load State    networkidle

    ${count}=    Get Element Count    ${JOB_CARD}
    ${page_text}=    Get Text    css=body
    ${page_text}=    Convert To Lower Case    ${page_text}
    ${has_special_message}=    Run Keyword And Return Status    Should Contain    ${page_text}    no result
    IF    not ${has_special_message}
        ${has_special_message}=    Run Keyword And Return Status    Should Contain    ${page_text}    no jobs
    END
    IF    not ${has_special_message}
        ${has_special_message}=    Run Keyword And Return Status    Should Contain    ${page_text}    invalid
    END
    IF    ${count} == 0
        Log To Console    Special character search returned no job cards, which is acceptable.
    ELSE IF    ${has_special_message}
        Log To Console    Special character search displayed a no-result or validation state.
    ELSE
        Fail    Special character search should show validation or no results, but job cards are still visible.
    END

Verify Search Returns No Results
    [Arguments]    ${search_text}
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible
    Fill Text    ${SEARCH_JOBS_INPUT}    ${search_text}
    Wait For Load State    networkidle

    ${count}=    Get Element Count    ${JOB_CARD}
    ${page_text}=    Get Text    css=body
    ${page_text}=    Convert To Lower Case    ${page_text}
    ${has_no_result_message}=    Run Keyword And Return Status    Should Contain    ${page_text}    no result
    IF    not ${has_no_result_message}
        ${has_no_result_message}=    Run Keyword And Return Status    Should Contain    ${page_text}    no jobs
    END
    IF    ${count} == 0
        Log To Console    No-result search returned zero cards as expected.
    ELSE IF    ${has_no_result_message}
        Log To Console    No-result search displayed a matching message.
    ELSE
        Fail    No-result search should show no results, but job cards are still visible.
    END

Verify Empty Search Shows All Records
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible
    Fill Text    ${SEARCH_JOBS_INPUT}    ${EMPTY}
    Wait For Load State    networkidle
    ${count}=    Get Element Count    ${JOB_CARD}
    Should Be True    ${count} > 0    Empty search should keep all records visible.

Verify Filter Change Updates Results
    Wait For Elements State    ${JOB_TYPE_DROPDWON}    visible    timeout=20s
    Click    ${JOB_TYPE_DROPDWON}
    Wait For Elements State    ${FIELD_FULL_TIME}    visible    timeout=20s
    Click    ${FIELD_FULL_TIME}
    Wait For Load State    networkidle

    Wait For Elements State    ${JOB_TYPE_DROPDWON}    visible    timeout=20s
    Click    ${JOB_TYPE_DROPDWON}
    Wait For Elements State    ${FIELD_PART_TIME}    visible    timeout=20s
    Click    ${FIELD_PART_TIME}
    Wait For Load State    networkidle

    ${count}=    Get Element Count    ${JOB_CARD}
    Should Be True    ${count} > 0    Part-time filter should show job cards.

    FOR    ${index}    IN RANGE    ${count}
        ${card_text}=    Get Text    ${JOB_CARD} >> nth=${index}
        ${card_text}=    Convert To Lower Case    ${card_text}
        Should Contain    ${card_text}    part-time
    END

Verify Jobs Only
    ${job_count}=    Get Element Count    ${JOB_LABEL}
    Log To Console    JOB COUNT = ${job_count}

    ${internship_count}=    Get Element Count    ${INTERNSHIP_LABEL}
    Log To Console    INTERNSHIP COUNT = ${internship_count}

Apply Job Type Filter
    [Arguments]    ${job_type}
    Wait For Elements State    ${JOB_TYPE_DROPDWON}    visible    timeout=20s
    Click    ${JOB_TYPE_DROPDWON}

    Wait For Elements State    ${job_type}    visible    timeout=20s
    Click    ${job_type}

    Wait For Load State    networkidle
    Sleep    2s

Verify Select Job Type Filter
    [Arguments]    ${job_type}    ${job_type_label}

    Log To Console    job_type=${job_type}
    Log To Console    job_type_label=${job_type_label}

    Apply Job Type Filter    ${job_type}

    ${count}=    Get Element Count    ${JOB_CARD}
    Log To Console    Total Cards=${count}

    IF    ${count} == 0
        Wait For Elements State    ${JOB_TYPE_DROPDWON}    visible    timeout=20s
    END

    FOR    ${index}    IN RANGE    ${count}
        Log To Console    Verifying Job Type Card ${index}
        Apply Job Type Filter    ${job_type}
        Wait For Elements State    ${JOB_CARD} >> nth=${index}    visible    timeout=20s
        Scroll To Element    ${JOB_CARD} >> nth=${index}
        Click    ${JOB_CARD} >> nth=${index}
        Wait For Elements State    ${JOB_DETAIL_APPLY_BUTTON}    visible    timeout=20s
        Wait For Elements State    ${job_type_label}    visible    timeout=20s
        Go To    ${BASE_URL}/jobs
        Wait For Elements State    ${EXPLORE_CAREER_TAB_ALL}    visible    timeout=20s
    END

Apply Work Mode Filter
    [Arguments]    ${work_mode}
    Wait For Elements State    ${WORK_MODE_DROPDOWN}    visible    timeout=30s
    Click    ${WORK_MODE_DROPDOWN}

    Wait For Elements State    ${work_mode}    visible    timeout=20s
    Click    ${work_mode}

    Wait For Load State    networkidle
    Sleep    2s

Verify Select Work Mode Filter
    [Arguments]    ${work_mode}    ${work_mode_label}

    Log To Console    work_mode=${work_mode}
    Log To Console    work_mode_label=${work_mode_label}

    Apply Work Mode Filter    ${work_mode}

    ${count}=    Get Element Count    ${JOB_CARD}
    Log To Console    Total Cards=${count}

    IF    ${count} == 0
        Wait For Elements State    ${WORK_MODE_DROPDOWN}    visible    timeout=20s
    END

    FOR    ${index}    IN RANGE    ${count}
        Log To Console    Verifying Work Mode Card ${index}
        Apply Work Mode Filter    ${work_mode}
        Wait For Elements State    ${JOB_CARD} >> nth=${index}    visible    timeout=20s
        Scroll To Element    ${JOB_CARD} >> nth=${index}
        Click    ${JOB_CARD} >> nth=${index}
        Wait For Elements State    ${JOB_DETAIL_APPLY_BUTTON}    visible    timeout=20s
        Wait For Elements State    ${work_mode_label}    visible    timeout=20s
        Go To    ${BASE_URL}/jobs
        Wait For Elements State    ${EXPLORE_CAREER_TAB_ALL}    visible    timeout=20s
    END

Verify Pagination Does Not Change
    Wait For Elements State    ${PAGINATION_PAGE_NUMBERS_1}    visible
    ${before}=    Get Text    ${PAGINATION_PAGE_NUMBERS_1}
    Click    ${PAGINATION_PREVIOUS_BUTTON}    
    ${after}=    Get Text    ${PAGINATION_PAGE_NUMBERS_1}
    Should Be Equal    ${before}    ${after}

Verify Next Page
    Wait For Elements State    ${PAGINATION_NEXT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${PAGINATION_NEXT_BUTTON}
    Wait For Elements State    ${PAGINATION_PAGE_NUMBERS_2}    visible

Verify Previous Page
    Wait For Elements State    ${PAGINATION_PREVIOUS_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${PAGINATION_PREVIOUS_BUTTON}
    Wait For Elements State    ${PAGINATION_PAGE_NUMBERS_1}    visible
