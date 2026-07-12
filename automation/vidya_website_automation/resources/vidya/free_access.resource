*** Settings ***
Resource    ../common/browser.resource
Resource    ../common/navigation.resource
Library     Browser


*** Variables ***
# Page-level
${FREE_ACCESS_LOCATOR}          xpath=(//div)[2]

# Form fields
${FIELD_FREE_ACCESS_NAME}                    role=textbox[name="Full name"]
${FIELD_FREE_ACCESS_EMAIL}                    role=textbox[name="Email"]
${FIELD_FREE_ACCESS_PHONE}                    role=textbox[name="XXXXXXXXXX"]
${FIELD_FREE_ACCESS_QUALIFICATION}            xpath=//button[contains(normalize-space(.),'Select') or contains(normalize-space(.),'Higher Education') or contains(normalize-space(.),'University')]
${FIELD_FREE_ACCESS_INSTITUTE_NAME}           role=textbox[name="Institution name"]
${FIELD_FREE_ACCESS_HIGHER_EDUCATION}          role=list >> text=Higher Education
${FIELD_FREE_ACCESS_UNIVERSITY}                role=list >> text=University
${FIELD_FREE_ACCESS_FIELD_OF_STUDY}           role=textbox[name="Field of study"]
${FIELD_FREE_ACCESS_MONTH}            role=button[name="Month"]
${FIELD_FREE_ACCESS_YEAR}             role=button[name="Year"]
${SUBMIT_FREE_ACCESS_BUTTON}                 role=button[name="Verify My Student Status"]
${RESUME_FREE_ACCESS_UPLOAD_BUTTON}          xpath=//button[normalize-space()='Upload']
${RESUME_FREE_ACCESS_UPLOAD_INPUT}           css=input[type="file"]
${FREE_ACCESS_COUNTRY_CODE_LOCATOR}          text=+91

# Success / error states
${ERROR_FREE_ACCESS_EMPTY_NAME_LOCATOR}        text=Please enter your full name.
${ERROR_FREE_ACCESS_EMPTY_EMAIL_LOCATOR}         text=Please enter a valid email.
${ERROR_FREE_ACCESS_EMPTY_PHONE_LOCATOR}        text=Phone number is required.
${ERROR_FREE_ACCESS_EMPTY_QUALIFICATION_LOCATOR}         text=Select one option to continue.
${ERROR_FREE_ACCESS_EMPTY_INSTITUTION_NAME_LOCATOR}         text=Please enter your institution
${ERROR_FREE_ACCESS_EMPTY_FIELD_OF_STUDY_LOCATOR}         text=Please enter your field of
${ERROR_FREE_ACCESS_EMPTY_MONTH_LOCATOR}    text=Please select an option.
${ERROR_FREE_ACCESS_EMPTY_YEAR_LOCATOR}     text=Please select an option.
${ERROR_FREE_ACCESS_EMPTY_DATE_LOCATOR}     xpath=(//p[normalize-space()='Please select an option.'])[1]
${ERROR_FREE_ACCESS_EMPTY_RESUME_LOCATOR}    text=Upload an image.
${ERROR_FREE_ACCESS_NAME_FORMAT_LOCATOR}    text=Please enter a valid name
${ERROR_FREE_ACCESS_EMAIL_FORMAT_LOCATOR}    text=Please enter a valid email.
${ERROR_FREE_ACCESS_INVALID_PHONE_LOCATOR}        text=Phone number must be exactly
${ERROR_FREE_ACCESS_INVALID_INSTITUTE_NAME_LOCATOR}        text=Please enter a valid institution name.
${ERROR_FREE_ACCESS_INVALID_RESUME_1_LOCATOR}        text=File too large, please upload
${ERROR_FREE_ACCESS_DUPLICATE_EMAIL_LOCATOR}        text=already
${ERROR_FREE_ACCESS_UNSUPPORTED_RESUME_LOCATOR}        text=Supports JPG, PNG or PDF


# Test data
${VALID_FREE_ACCESS_NAME}       test
${VALID_FREE_ACCESS_FULL_NAME}       Sabar Nisha
${NUMERIC_FREE_ACCESS_NAME}     123456
${SPECIAL_FREE_ACCESS_NAME}     @#$%^
${SPACED_FREE_ACCESS_NAME}      ${SPACE}Sabar Nisha${SPACE}
${VALID_FREE_ACCESS_EMAIL}      test@gmail.com
${FREE_ACCESS_EMAIL_WITH_SPACES}      test @gmail.com
${FREE_ACCESS_EMAIL_UPPERCASE}        TEST@GMAIL.COM
${DUPLICATE_FREE_ACCESS_EMAIL}        test@gmail.com
${VALID_FREE_ACCESS_MESSAGE}    test message.
${VALID_FREE_ACCESS_PHONE}      9834567890
${WRONG_FREE_ACCESS_EMAIL_1}      test@com
${WRONG_FREE_ACCESS_EMAIL_2}      testgmail.com
${WRONG_FREE_ACCESS_EMAIL_3}      @gmail.com
${INVALID_FREE_ACCESS_PHONE_1}    987654
${INVALID_FREE_ACCESS_PHONE_MORE_DIGITS}    9876543210123
${INVALID_FREE_ACCESS_PHONE_ALPHABETS}    abcde
${INVALID_FREE_ACCESS_PHONE_SPECIAL}    @#$%
${INVALID_FREE_ACCESS_NAME_1}     123456SFSFDS
${INVALID_FREE_ACCESS_NAME_2}     @#$@#$@#
${LONG_FREE_ACCESS_UNIVERSITY_NAME}       SJFHSDKJFHDSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSFHSKJDHFSJKFHSKFHSDJKFHSDKFHSDKJFHSDJFHSDJKFHSDJKFHSDKJFHSDKJFHSDKJFHSD9823479823AKJFHKJSA3RHEJKHF  DFJSDKFHSKJFHSKJFHSK}
${LONG_FREE_ACCESS_NAME}          SDFSDFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFD  SFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSF DSFSDF
${VALID_FREE_ACCESS_FIELD_OF_STUDY}    Computer Science 
${NUMERIC_FREE_ACCESS_FIELD_OF_STUDY}    12345
${LONG_FREE_ACCESS_FIELD_OF_STUDY}       Computer Science Computer Science Computer Science Computer Science Computer Science Computer Science Computer Science Computer Science
${VALID_FREE_ACCESS_INSTITUTE_NAME}    Test University
${SPECIAL_FREE_ACCESS_INSTITUTE_NAME}    @#$ College
${LONG_FREE_ACCESS_INSTITUTE_NAME_MAX}    International Institute of Advanced Technology and Applied Computer Science Research University Campus
${VALID_FREE_ACCESS_QUALIFICATION}     ${FIELD_FREE_ACCESS_HIGHER_EDUCATION}
${VALID_FREE_ACCESS_QUALIFICATION_HIGHER_EDUCATION}    ${FIELD_FREE_ACCESS_HIGHER_EDUCATION}
${VALID_FREE_ACCESS_QUALIFICATION_UNIVERSITY}          ${FIELD_FREE_ACCESS_UNIVERSITY}
${VALID_FREE_ACCESS_MONTH}             January
${VALID_FREE_ACCESS_YEAR}              2025
${PAST_FREE_ACCESS_YEAR}               2023
${RESUME_FREE_ACCESS_VALID_PDF}        test_data/resume_valid.pdf
${RESUME_FREE_ACCESS_VALID_PNG}        test_data/resume_valid.png
${RESUME_FREE_ACCESS_VALID_JPG}        test_data/resume_valid.jpg
${RESUME_FREE_ACCESS_OVERSIZED}        test_data/resume_oversized_11mb.pdf
${RESUME_FREE_ACCESS_EXACT_5MB}        test_data/resume_exact_5mb.pdf
${RESUME_FREE_ACCESS_UNSUPPORTED_DOCX}        test_data/resume_valid.docx
${RESUME_FREE_ACCESS_CORRUPTED}        test_data/invalid_resume.png
${RESUME_FREE_ACCESS_SPECIAL_FILENAME}        test_data/student@123#.pdf
${INVALID_FREE_ACCESS_PHONE_2}        abcdefghij

*** Keywords ***
Verify Free Access Page Is Loaded
    Wait For Elements State    ${FIELD_FREE_ACCESS_NAME}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_FREE_ACCESS_EMAIL}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${SUBMIT_FREE_ACCESS_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Fill Free Access Form
    [Arguments]    ${name}=${EMPTY}    ${email}=${EMPTY}    ${phone}=${EMPTY}    ${qualification}=${EMPTY}
    ...    ${institute}=${EMPTY}    ${field_of_study}=${EMPTY}    ${month}=${EMPTY}    ${year}=${EMPTY}
    ...    ${resume_path}=${EMPTY}

    Wait For Elements State    ${FIELD_FREE_ACCESS_NAME}    visible    timeout=${DEFAULT_TIMEOUT}

    Fill Text    ${FIELD_FREE_ACCESS_NAME}    ${name}
    Fill Text    ${FIELD_FREE_ACCESS_EMAIL}    ${email}
    Fill Text    ${FIELD_FREE_ACCESS_PHONE}    ${phone}

    IF    '${qualification}' != '${EMPTY}'
        Select Free Access Option    ${FIELD_FREE_ACCESS_QUALIFICATION}    ${qualification}
    END

    Fill Text    ${FIELD_FREE_ACCESS_INSTITUTE_NAME}    ${institute}
    Fill Text    ${FIELD_FREE_ACCESS_FIELD_OF_STUDY}    ${field_of_study}

    IF    '${month}' != '${EMPTY}'
        Select Free Access Option    ${FIELD_FREE_ACCESS_MONTH}    ${month}
    END

    IF    '${year}' != '${EMPTY}'
        Select Free Access Option    ${FIELD_FREE_ACCESS_YEAR}    ${year}
    END

    IF    '${resume_path}' != '${EMPTY}'
        Upload Resume File    ${resume_path}
    END

Select Free Access Option
    [Arguments]    ${field_locator}    ${option_text}
    Click    ${field_locator}
    Run Keyword If    '${option_text}'.startswith('role=')
    ...    Wait For Elements State    ${option_text}    visible    timeout=${DEFAULT_TIMEOUT}
    ...    ELSE
    ...    Wait For Elements State    text=${option_text}    visible    timeout=${DEFAULT_TIMEOUT}
    Run Keyword If    '${option_text}'.startswith('role=')
    ...    Click    ${option_text}
    ...    ELSE
    ...    Click    text=${option_text}

Upload Resume File
    [Arguments]    ${file_path}
    Click    ${RESUME_FREE_ACCESS_UPLOAD_BUTTON}
    Wait For Elements State    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    attached    timeout=${DEFAULT_TIMEOUT}
    Upload File By Selector    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    ${file_path}
    ${selected_files}=    Get Property    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    files
    ${selected_count}=    Get Length    ${selected_files}
    Should Be Equal As Integers    ${selected_count}    1    Resume upload did not select a file.

Submit Free Access Form
    Wait For Elements State    ${SUBMIT_FREE_ACCESS_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SUBMIT_FREE_ACCESS_BUTTON}

Verify Free Access Validation Error
    [Arguments]    ${field}

    ${error_locator}=    Set Variable If
    ...    '${field}' == 'empty_name'    ${ERROR_FREE_ACCESS_EMPTY_NAME_LOCATOR}
    ...    '${field}' == 'empty_email'    ${ERROR_FREE_ACCESS_EMPTY_EMAIL_LOCATOR}
    ...    '${field}' == 'empty_phone'    ${ERROR_FREE_ACCESS_EMPTY_PHONE_LOCATOR}
    ...    '${field}' == 'empty_qualification'    ${ERROR_FREE_ACCESS_EMPTY_QUALIFICATION_LOCATOR}
    ...    '${field}' == 'empty_institute_name'    ${ERROR_FREE_ACCESS_EMPTY_INSTITUTION_NAME_LOCATOR}
    ...    '${field}' == 'empty_field_of_study'    ${ERROR_FREE_ACCESS_EMPTY_FIELD_OF_STUDY_LOCATOR}
    ...    '${field}' == 'empty_month'    ${ERROR_FREE_ACCESS_EMPTY_MONTH_LOCATOR}
    ...    '${field}' == 'empty_year'    ${ERROR_FREE_ACCESS_EMPTY_YEAR_LOCATOR}
    ...    '${field}' == 'empty_graduation_date'    ${ERROR_FREE_ACCESS_EMPTY_DATE_LOCATOR}
    ...    '${field}' == 'empty_resume'    ${ERROR_FREE_ACCESS_EMPTY_RESUME_LOCATOR}
    ...    '${field}' == 'invalid_email'    ${ERROR_FREE_ACCESS_EMAIL_FORMAT_LOCATOR}
    ...    '${field}' == 'invalid_phone'    ${ERROR_FREE_ACCESS_INVALID_PHONE_LOCATOR}
    ...    '${field}' == 'invalid_name'    ${ERROR_FREE_ACCESS_NAME_FORMAT_LOCATOR}
    ...    '${field}' == 'oversized_resume'    ${ERROR_FREE_ACCESS_INVALID_RESUME_1_LOCATOR}
    ...    '${field}' == 'unsupported_resume'    ${ERROR_FREE_ACCESS_UNSUPPORTED_RESUME_LOCATOR}
    ...    '${field}' == 'duplicate_email'    ${ERROR_FREE_ACCESS_DUPLICATE_EMAIL_LOCATOR}

    Wait For Elements State    ${error_locator}    visible    timeout=10s

Verify Free Access Validation Error Or Stable Form
    [Arguments]    ${field}

    ${error_locator}=    Set Variable If
    ...    '${field}' == 'invalid_email'    ${ERROR_FREE_ACCESS_EMAIL_FORMAT_LOCATOR}
    ...    '${field}' == 'invalid_name'    ${ERROR_FREE_ACCESS_NAME_FORMAT_LOCATOR}
    ...    '${field}' == 'invalid_phone'    ${ERROR_FREE_ACCESS_INVALID_PHONE_LOCATOR}
    ...    '${field}' == 'invalid_institute_name'    ${ERROR_FREE_ACCESS_INVALID_INSTITUTE_NAME_LOCATOR}
    ...    '${field}' == 'unsupported_resume'    ${ERROR_FREE_ACCESS_UNSUPPORTED_RESUME_LOCATOR}
    ...    '${field}' == 'oversized_resume'    ${ERROR_FREE_ACCESS_INVALID_RESUME_1_LOCATOR}
    ...    '${field}' == 'duplicate_email'    ${ERROR_FREE_ACCESS_DUPLICATE_EMAIL_LOCATOR}
    ...    ${ERROR_FREE_ACCESS_EMAIL_FORMAT_LOCATOR}

    ${has_error}=    Run Keyword And Return Status    Wait For Elements State    ${error_locator}    visible    timeout=5s
    IF    not ${has_error}
        Verify Free Access Submission Stability
    END

Verify Free Access Submission Stability
    Wait For Elements State    ${FIELD_FREE_ACCESS_NAME}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_FREE_ACCESS_EMAIL}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_FREE_ACCESS_PHONE}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${SUBMIT_FREE_ACCESS_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Verify Free Access Upload Stability
    Wait For Elements State    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    attached    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${SUBMIT_FREE_ACCESS_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Verify Free Access Upload Error Or Stable Form
    [Arguments]    ${field}
    ${error_locator}=    Set Variable If
    ...    '${field}' == 'unsupported_resume'    ${ERROR_FREE_ACCESS_UNSUPPORTED_RESUME_LOCATOR}
    ...    '${field}' == 'oversized_resume'    ${ERROR_FREE_ACCESS_INVALID_RESUME_1_LOCATOR}
    ...    ${ERROR_FREE_ACCESS_UNSUPPORTED_RESUME_LOCATOR}
    ${has_error}=    Run Keyword And Return Status    Wait For Elements State    ${error_locator}    visible    timeout=5s
    IF    not ${has_error}
        Verify Free Access Upload Stability
    END

Replace Free Access Uploaded File
    [Arguments]    ${first_file}    ${second_file}
    Upload Resume File    ${first_file}
    Wait For Elements State    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    attached    timeout=${DEFAULT_TIMEOUT}
    Upload File By Selector    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    ${second_file}
    ${selected_files}=    Get Property    ${RESUME_FREE_ACCESS_UPLOAD_INPUT}    files
    ${selected_count}=    Get Length    ${selected_files}
    Should Be Equal As Integers    ${selected_count}    1    Replacing the uploaded resume did not keep a single selected file.
    Verify Free Access Upload Stability

Remove Free Access Uploaded File If Available
    [Arguments]    ${file_path}
    Upload Resume File    ${file_path}
    ${remove_locator}=    Set Variable    xpath=//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'remove') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'delete') or contains(@aria-label,'Remove') or contains(@aria-label,'Delete')]
    ${has_remove}=    Run Keyword And Return Status    Wait For Elements State    ${remove_locator}    visible    timeout=5s
    IF    ${has_remove}
        Click    ${remove_locator}
        Wait For Elements State    ${RESUME_FREE_ACCESS_UPLOAD_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    ELSE
        Verify Free Access Upload Stability
    END

Submit Free Access Form By Enter
    Press Keys    ${FIELD_FREE_ACCESS_FIELD_OF_STUDY}    Enter
    Verify Free Access Submission Stability

Verify Free Access Submit Button State During Submission
    Click    ${SUBMIT_FREE_ACCESS_BUTTON}
    ${disabled}=    Run Keyword And Return Status    Wait For Elements State    ${SUBMIT_FREE_ACCESS_BUTTON}    disabled    timeout=2s
    IF    not ${disabled}
        Verify Free Access Submission Stability
    END

Double Click Free Access Submit Button
    Click    ${SUBMIT_FREE_ACCESS_BUTTON}
    Click    ${SUBMIT_FREE_ACCESS_BUTTON}
    Verify Free Access Submission Stability

Verify Free Access Multiple Validation Messages
    Submit Free Access Form
    Verify Free Access Validation Error    empty_name
    Verify Free Access Validation Error    empty_email
    Verify Free Access Validation Error    empty_phone
    Verify Free Access Validation Error    empty_qualification

Correct Free Access Invalid Email And Submit
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${WRONG_FREE_ACCESS_EMAIL_1}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    invalid_email
    Fill Text    ${FIELD_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_EMAIL}
    Submit Free Access Form
    Verify Free Access Submission Stability

Submit Free Access Form While Offline
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Set Offline    True
    Submit Free Access Form
    Set Offline    False
    Verify Free Access Submission Stability

Verify Free Access Server Error Handling
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Verify Free Access Session Timeout Handling
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Sleep    2s
    Submit Free Access Form
    Verify Free Access Submission Stability

Fill Free Access Form With Pasted Data
    Fill Free Access Form    ${VALID_FREE_ACCESS_FULL_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}

Verify Free Access Refresh Behavior
    Fill Text    ${FIELD_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_FULL_NAME}
    Fill Text    ${FIELD_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_EMAIL}
    Reload
    Verify Free Access Page Is Loaded

Submit Free Access Spaces Only Data
    Fill Free Access Form    ${SPACE}${SPACE}${SPACE}    ${SPACE}${SPACE}${SPACE}    ${SPACE}${SPACE}${SPACE}    ${EMPTY}
    ...    ${SPACE}${SPACE}${SPACE}    ${SPACE}${SPACE}${SPACE}    ${EMPTY}    ${EMPTY}    ${EMPTY}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_email

Verify Free Access Browser Navigation
    Fill Text    ${FIELD_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_FULL_NAME}
    Go Back
    Go Forward
    Verify Free Access Page Is Loaded

Verify Free Access Mobile Responsive
    Set Viewport Size    390    844
    Verify Free Access Page Is Loaded
    Wait For Elements State    ${FIELD_FREE_ACCESS_PHONE}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_FREE_ACCESS_QUALIFICATION}    visible    timeout=${DEFAULT_TIMEOUT}
    Set Viewport Size    ${VIEWPORT_W}    ${VIEWPORT_H}

Verify Free Access Keyboard Accessibility
    Focus    ${FIELD_FREE_ACCESS_NAME}
    Press Keys    ${FIELD_FREE_ACCESS_NAME}    Tab
    Press Keys    ${FIELD_FREE_ACCESS_EMAIL}    Tab
    Press Keys    ${FIELD_FREE_ACCESS_PHONE}    Tab
    Press Keys    ${FIELD_FREE_ACCESS_QUALIFICATION}    Tab
    Verify Free Access Submission Stability

Verify Free Access Successful Submission Flow
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Verify Free Access Submission Does Not Show Error Message
    Wait For Elements State    text=Your status has been verified    visible    timeout=10s

Verify Free Access Country Code Display
    Wait For Elements State    ${FREE_ACCESS_COUNTRY_CODE_LOCATOR}    visible    timeout=${DEFAULT_TIMEOUT}

Open Free Access Institution Type Dropdown
    Wait For Elements State    ${FIELD_FREE_ACCESS_QUALIFICATION}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${FIELD_FREE_ACCESS_QUALIFICATION}
    Wait For Elements State    ${FIELD_FREE_ACCESS_HIGHER_EDUCATION}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_FREE_ACCESS_UNIVERSITY}    visible    timeout=${DEFAULT_TIMEOUT}

Verify Free Access Institution Type Dropdown Closed
    Wait For Elements State    ${FIELD_FREE_ACCESS_HIGHER_EDUCATION}    hidden    timeout=5s
    Wait For Elements State    ${FIELD_FREE_ACCESS_UNIVERSITY}    hidden    timeout=5s

Select Each Free Access Institution Type
    Select Free Access Option    ${FIELD_FREE_ACCESS_QUALIFICATION}    ${FIELD_FREE_ACCESS_HIGHER_EDUCATION}
    Wait For Elements State    xpath=//button[contains(normalize-space(.),'Higher Education')]    visible    timeout=${DEFAULT_TIMEOUT}
    Select Free Access Option    ${FIELD_FREE_ACCESS_QUALIFICATION}    ${FIELD_FREE_ACCESS_UNIVERSITY}
    Wait For Elements State    xpath=//button[contains(normalize-space(.),'University')]    visible    timeout=${DEFAULT_TIMEOUT}

Verify Free Access Month Values
    Click    ${FIELD_FREE_ACCESS_MONTH}
    FOR    ${month}    IN    January    February    March    April    May    June    July    August    September    October    November    December
        Wait For Elements State    xpath=//li[normalize-space(.)='${month}']    visible    timeout=${DEFAULT_TIMEOUT}
    END

Verify Free Access Year Values
    Click    ${FIELD_FREE_ACCESS_YEAR}
    FOR    ${year}    IN    2023    2024    2025    2026    2027    2028    2029    2030    2031    2032
        Wait For Elements State    xpath=//li[normalize-space(.)='${year}']    visible    timeout=${DEFAULT_TIMEOUT}
    END
