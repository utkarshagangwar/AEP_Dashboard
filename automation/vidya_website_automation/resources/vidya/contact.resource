*** Settings ***
Resource    ../common/browser.resource
Resource    ../common/navigation.resource
Library     Browser


*** Variables ***
# Page-level
${CONTACT_MAIN_HEADING}          role=link[name="Contact Us"]

# Form fields
${CONTACT_FORM_LOCATOR}          text=Get in touch with Vidya
${FIELD_NAME}                    role=textbox[name="Enter your full name"]
${FIELD_EMAIL}                   role=textbox[name="Email Address"]
${FIELD_PHONE}                   role=textbox[name="xxxxx xxxxx"]
${FIELD_MESSAGE}                 role=textbox[name="Message/Inquiry"]
${FIELD_USER_TYPE}                xpath=//form//button[not(@type='submit')][1]
${FIELD_WORKING_PROFESSIONAL}     css=li[data-value="Working Professional"]
${FIELD_INSTITUTION}              css=li[data-value="Institution"]
${FIELD_INSTITUTION_NAME}         role=textbox[name="Enter institution name"]
${FIELD_ORGANIZATION}             css=li[data-value="Organization"]
${FIELD_ORGANIZATION_NAME}        role=textbox[name="Enter organization name"]
${FIELD_STUDENT}                   css=li[data-value="Student"]
${SUBMIT_BUTTON}                 role=button[name="Submit"]

# Additional visible elements / placeholders
${USER_TYPE_DROPDOWN_MENU}        ${EMPTY}
${COUNTRY_CODE_LOCATOR}           ${EMPTY}
${COUNTRY_CODE_VALUE}             +91
${SUCCESS_MESSAGE_LOCATOR}        ${EMPTY}
${SUCCESS_TOAST_LOCATOR}          ${EMPTY}
${FORM_RESET_LOCATOR}             ${EMPTY}

# Optional validation message placeholders 
${ERROR_NAME_NUMBER_LOCATOR}      ${EMPTY}
${ERROR_NAME_SPECIAL_LOCATOR}     ${EMPTY}
${ERROR_EMAIL_MULTIPLE_AT_LOCATOR}    ${EMPTY}
${ERROR_EMAIL_SPACES_LOCATOR}     ${EMPTY}
${ERROR_PHONE_MIN_LOCATOR}        ${EMPTY}
${ERROR_PHONE_MAX_LOCATOR}        ${EMPTY}
${ERROR_PHONE_ALPHA_LOCATOR}      ${EMPTY}
${ERROR_PHONE_SPECIAL_LOCATOR}    ${EMPTY}
${ERROR_MESSAGE_ONLY_SPACES_LOCATOR}    ${EMPTY}
${ERROR_SQL_INJECTION_LOCATOR}    ${EMPTY}
${ERROR_XSS_LOCATOR}              ${EMPTY}
${ERROR_EMOJI_LOCATOR}            ${EMPTY}

# Success / error states
${ERROR_EMPTY_NAME_LOCATOR}        text=Please enter your full name
${ERROR_EMAIL_FORMAT_LOCATOR}    text=Please enter a valid email
${ERROR_NAME_FORMAT_LOCATOR}    text=Please enter a valid name
${ERROR_EMPTY_EMAIL_LOCATOR}         text=Please enter your email
${ERROR_EMPTY_MESSAGE_LOCATOR}      text=Please share a brief message
${ERROR_EMPTY_PHONE_LOCATOR}        text=Please enter a valid 10 digit
${ERROR_USER_TYPE_LOCATOR}           text=Invalid input: expected
${ERROR_EMPTY_INSTITUTION_NAME_LOCATOR}    text=Please enter your institution
${ERROR_EMPTY_ORGANIZATION_NAME_LOCATOR}    text=Please enter your organization name


# Test data
${VALID_NAME}       test
${VALID_EMAIL}      test@gmail.com
${VALID_MESSAGE}    test message.
${VALID_PHONE}      1234567890
${WRONG_EMAIL_1}      test@com
${WRONG_EMAIL_2}      testgamail.com
${INVALID_PHONE_1}    SKJFKSDJHFSJ
${INVALID_PHONE_2}    @$@#$@#
${INVALID_NAME_1}     12345668
${INVALID_NAME_2}     @#$@#$@#
${LONG_MESSAGE}       SJFHSDKJFHDSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSFHSKJDHFSJKFHSKFHSDJKFHSDKFHSDKJFHSDJFHSDJKFHSDJKFHSDKJFHSDKJFHSDKJFHSD9823479823AKJFHKJSA3RHEJKHF  DFJSDKFHSKJFHSKJFHSK}
${LONG_NAME}          SDFSDFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFD  SFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSFDSF DSFSDF
${VALID_NAME_FULL}  John Doe
${VALID_NAME_MIN}   A
${NAME_WITH_NUMBERS}   John123
${NAME_WITH_SPECIAL}   John@Doe
${EMAIL_WITH_MULTIPLE_AT}   test@@example.com
${EMAIL_WITH_SPACES}        test @example.com
${EMAIL_UPPERCASE}          TEST.USER@EXAMPLE.COM
${PHONE_LESS_DIGITS}        12345
${PHONE_MORE_DIGITS}        12345678901
${PHONE_WITH_ALPHABETS}     12345abcde
${PHONE_WITH_SPECIAL}       12345-6789
${MESSAGE_WITH_SPECIAL}     Need help! @#$% & *
${MESSAGE_ONLY_SPACES}      ${SPACE}${SPACE}${SPACE}
${MULTI_LINE_MESSAGE}       First line${\n}Second line
${SQL_INJECTION_TEXT}       SELECT * FROM users WHERE 1=1
${XSS_SCRIPT_TEXT}          <script>alert('x')</script>
${EMOJI_TEXT}               😊 Test 🙌


*** Keywords ***
Verify Contact Page Is Loaded
    ${title}=    Get Title
    Should Not Be Empty    ${title}
    Wait For Elements State    ${CONTACT_FORM_LOCATOR}    visible    timeout=${DEFAULT_TIMEOUT}

Fill Contact Form
    [Arguments]    ${name}    ${email}    ${message}    ${phone}    ${user_type}=${EMPTY}    ${user_type_name}=${EMPTY}
    Wait For Elements State    ${FIELD_NAME}       visible    timeout=${DEFAULT_TIMEOUT}
    Fill Text    ${FIELD_NAME}       ${name}
    Fill Text    ${FIELD_EMAIL}      ${email}
    Fill Text    ${FIELD_MESSAGE}    ${message}
    Fill Text    ${FIELD_PHONE}      ${phone}
    Run Keyword If    '${user_type}' != '${EMPTY}'    Select User Type    ${user_type}
    Run Keyword If    '${user_type}' == 'Institution'    Fill Text    ${FIELD_INSTITUTION_NAME}    ${user_type_name}
    Run Keyword If    '${user_type}' == 'Organization'    Fill Text    ${FIELD_ORGANIZATION_NAME}   ${user_type_name}

Select User Type
    [Arguments]    ${user_type}
    Click    ${FIELD_USER_TYPE}
    ${option_locator}=    Set Variable If    '${user_type}' == 'Working Professional'    ${FIELD_WORKING_PROFESSIONAL}
    ...    '${user_type}' == 'Institution'    ${FIELD_INSTITUTION}
    ...    '${user_type}' == 'Organization'    ${FIELD_ORGANIZATION}
    ...    '${user_type}' == 'Student'    ${FIELD_STUDENT}
    Wait For Elements State    ${option_locator}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${option_locator}

Submit Contact Form
    Wait For Elements State    ${SUBMIT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SUBMIT_BUTTON}


Verify Validation Error
    [Arguments]    ${field}
    ${error_locator}=    Set Variable If
    ...    '${field}' == 'email'    ${ERROR_EMAIL_FORMAT_LOCATOR}
    ...    '${field}' == 'phone'    ${ERROR_EMPTY_PHONE_LOCATOR}
    ...    '${field}' == 'name'    ${ERROR_NAME_FORMAT_LOCATOR}
    ...    '${field}' == 'empty_name'    ${ERROR_EMPTY_NAME_LOCATOR}
    ...    '${field}' == 'empty_email'    ${ERROR_EMPTY_EMAIL_LOCATOR}
    ...    '${field}' == 'empty_message'    ${ERROR_EMPTY_MESSAGE_LOCATOR}
    ...    '${field}' == 'user_type'    ${ERROR_USER_TYPE_LOCATOR}
    ...    '${field}' == 'institution_name'    ${ERROR_EMPTY_INSTITUTION_NAME_LOCATOR}
    ...    '${field}' == 'organization_name'    ${ERROR_EMPTY_ORGANIZATION_NAME_LOCATOR}
    ...    ${ERROR_EMAIL_FORMAT_LOCATOR}
    Wait For Elements State    ${error_locator}    visible    timeout=5s

Verify Contact Submission Stability
    Wait For Elements State    ${FIELD_NAME}       visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_EMAIL}      visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_PHONE}      visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${FIELD_MESSAGE}    visible    timeout=${DEFAULT_TIMEOUT}
    Wait For Elements State    ${SUBMIT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}

Clear Contact Form
    Wait For Elements State    ${FIELD_NAME}       visible    timeout=${DEFAULT_TIMEOUT}
    Fill Text    ${FIELD_NAME}       ${EMPTY}
    Fill Text    ${FIELD_EMAIL}      ${EMPTY}
    Fill Text    ${FIELD_MESSAGE}    ${EMPTY}
    Fill Text    ${FIELD_PHONE}      ${EMPTY}
    ${institution_visible}=    Run Keyword And Return Status    Wait For Elements State    ${FIELD_INSTITUTION_NAME}    visible    timeout=1s
    Run Keyword If    ${institution_visible}    Fill Text    ${FIELD_INSTITUTION_NAME}    ${EMPTY}
    ${organization_visible}=    Run Keyword And Return Status    Wait For Elements State    ${FIELD_ORGANIZATION_NAME}    visible    timeout=1s
    Run Keyword If    ${organization_visible}    Fill Text    ${FIELD_ORGANIZATION_NAME}   ${EMPTY}

Verify Validation Error On Empty Name
    Clear Contact Form
    Fill Text    ${FIELD_NAME}       ${EMPTY}
    Fill Text    ${FIELD_EMAIL}      ${VALID_EMAIL}
    Fill Text    ${FIELD_MESSAGE}    ${VALID_MESSAGE}
    Fill Text    ${FIELD_PHONE}      ${VALID_PHONE}
    Click    ${FIELD_USER_TYPE}
    Click    ${FIELD_STUDENT}
    Submit Contact Form
    Wait For Elements State    ${ERROR_EMPTY_NAME_LOCATOR}    visible    timeout=10s

Verify Validation Error On Wrong Email
    Clear Contact Form
    Fill Text    ${FIELD_NAME}       ${VALID_NAME}
    Fill Text    ${FIELD_EMAIL}      ${WRONG_EMAIL_1}
    Fill Text    ${FIELD_MESSAGE}    ${VALID_MESSAGE}
    Fill Text    ${FIELD_PHONE}      ${VALID_PHONE}
    Submit Contact Form
    Wait For Elements State    ${ERROR_EMAIL_FORMAT_LOCATOR}    visible    timeout=5s

Verify Successful Form Submission
    Clear Contact Form
    Fill Text    ${FIELD_NAME}       ${VALID_NAME}
    Fill Text    ${FIELD_EMAIL}      ${VALID_EMAIL}
    Fill Text    ${FIELD_MESSAGE}    ${VALID_MESSAGE}
    Fill Text    ${FIELD_PHONE}      ${VALID_PHONE}
    Click    ${FIELD_USER_TYPE}
    Click    ${FIELD_WORKING_PROFESSIONAL}
    Submit Contact Form
    # Assuming a success message appears after submission, we can wait for it here
    Wait For Elements State    text=Form successfully submitted    visible    timeout=10s

Verify Contact Submission Does Not Show Error Message
    Wait For Elements State    text=Form successfully submitted    visible    timeout=10s

#====new
Open User Type Dropdown
    Wait For Elements State    ${FIELD_USER_TYPE}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${FIELD_USER_TYPE}
    Run Keyword If    '${USER_TYPE_DROPDOWN_MENU}' != ''    Wait For Elements State    ${USER_TYPE_DROPDOWN_MENU}    visible    timeout=${DEFAULT_TIMEOUT}

Verify Country Code Display
    Run Keyword If    '${COUNTRY_CODE_LOCATOR}' != ''    Wait For Elements State    ${COUNTRY_CODE_LOCATOR}    visible    timeout=5s
    IF    '${COUNTRY_CODE_LOCATOR}' != ''
        ${code_text}=    Get Text    ${COUNTRY_CODE_LOCATOR}
        Should Be Equal    ${code_text}    ${COUNTRY_CODE_VALUE}
    END

Verify Success Message
    Run Keyword If    '${SUCCESS_MESSAGE_LOCATOR}' != ''    Wait For Elements State    ${SUCCESS_MESSAGE_LOCATOR}    visible    timeout=10s
    Run Keyword If    '${SUCCESS_TOAST_LOCATOR}' != ''    Wait For Elements State    ${SUCCESS_TOAST_LOCATOR}    visible    timeout=10s
    Run Keyword If    '${SUCCESS_MESSAGE_LOCATOR}' == '' and '${SUCCESS_TOAST_LOCATOR}' == ''    Log    No success locator provided; skipped success-message assertion.

Verify Form Reset After Submission
    ${name_value}=    Get Attribute    ${FIELD_NAME}    value
    ${email_value}=    Get Attribute    ${FIELD_EMAIL}    value
    ${phone_value}=    Get Attribute    ${FIELD_PHONE}    value
    ${message_value}=    Get Attribute    ${FIELD_MESSAGE}    value
    Should Be Equal    ${name_value}    ${EMPTY}
    Should Be Equal    ${email_value}    ${EMPTY}
    Should Be Equal    ${phone_value}    ${EMPTY}
    Should Be Equal    ${message_value}    ${EMPTY}
    Run Keyword If    '${FORM_RESET_LOCATOR}' != ''    Wait For Elements State    ${FORM_RESET_LOCATOR}    visible    timeout=5s

