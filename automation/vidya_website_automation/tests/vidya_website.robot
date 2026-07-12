*** Settings ***
Resource        ../resources/common/browser.resource
Resource        ../resources/common/navigation.resource
Resource        ../resources/vidya/contact.resource
Resource        ../resources/vidya/free_access.resource
Resource        ../resources/vidya/explore_career.resource
Variables       ../variables/vidya_vars.py

Suite Setup       Open Browser Session
Suite Teardown    Close Browser Session
Test Teardown     Take Screenshot On Fail


*** Test Cases ***

# ══════════════════════════════════════════════
# CONTACT PAGE TESTS
# ══════════════════════════════════════════════

Contact - Verify Contact Page Loads
    [Tags]    contact
    Navigate To Contact Page
    Verify Contact Page Is Loaded

Contact - Verify Validation Error On Empty Name
    [Tags]    contact
    Navigate To Contact Page
    Verify Validation Error On Empty Name

Contact - Verify Validation Error On Wrong Email
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}     ${WRONG_EMAIL_1}   ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Validation Error    email

Contact - Submit Institution type with empty institution name
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${VALID_MESSAGE}  ${VALID_PHONE}  Institution
    Submit Contact Form
    Verify Validation Error    institution_name

Contact - Submit Organization type with empty organization name
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${VALID_MESSAGE}  ${VALID_PHONE}  Organization
    Submit Contact Form
    Verify Validation Error    organization_name

Contact - Submit with empty email
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${EMPTY}  ${VALID_MESSAGE}  ${VALID_PHONE}  Working Professional
    Submit Contact Form
    Verify Validation Error    empty_email

Contact - Submit with empty phone
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${VALID_MESSAGE}  ${EMPTY}  Working Professional
    Submit Contact Form
    Verify Validation Error    phone

Contact - Submit with no user type selected
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${VALID_MESSAGE}  ${VALID_PHONE}
    Submit Contact Form
    Verify Validation Error    user_type

Contact - Submit with empty message
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${EMPTY}  ${VALID_PHONE}  Working Professional
    Submit Contact Form
    Verify Validation Error    empty_message

Contact - Submit with a long message
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form   ${VALID_NAME}  ${VALID_EMAIL}  ${LONG_MESSAGE}  ${VALID_PHONE}  Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Submit All Valid Inputs
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Full Name Trims Leading And Trailing Spaces
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${SPACE}${VALID_NAME_FULL}${SPACE}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Email Accepts Uppercase Letters
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${EMAIL_UPPERCASE}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Phone Rejects Too Few Digits
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${PHONE_LESS_DIGITS}    Working Professional
    Submit Contact Form
    Verify Validation Error    phone

Contact - Phone Rejects Too Many Digits
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${PHONE_MORE_DIGITS}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Phone Rejects Alphabets
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${PHONE_WITH_ALPHABETS}    Working Professional
    Submit Contact Form
    Verify Validation Error    phone

Contact - Phone Rejects Special Characters
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${PHONE_WITH_SPECIAL}    Working Professional
    Submit Contact Form
    Verify Validation Error    phone

Contact - Country Code Displayed By Default
    [Tags]    contact
    Navigate To Contact Page
    Verify Country Code Display

Contact - Open User Type Dropdown
    [Tags]    contact
    Navigate To Contact Page
    Open User Type Dropdown

Contact - Select Each User Type Option
    [Tags]    contact
    Navigate To Contact Page
    Select User Type    Working Professional
    Select User Type    Institution
    Select User Type    Organization
    Select User Type    Student

Contact - Message Accepts Special Characters
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${MESSAGE_WITH_SPECIAL}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Message Rejects Only Spaces
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${MESSAGE_ONLY_SPACES}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Message Supports Multi Line Input
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${MULTI_LINE_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - SQL Injection Is Not Executed
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${SQL_INJECTION_TEXT}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - XSS Injection Is Sanitized
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${XSS_SCRIPT_TEXT}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Emoji Input Validation
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${EMOJI_TEXT}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Stability

Contact - Browser Refresh During Entry
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Reload
    Verify Contact Page Is Loaded

Contact - Verify Correct Submission Does Not Show Problem Message
    [Tags]    contact
    Navigate To Contact Page
    Fill Contact Form    ${VALID_NAME_FULL}    ${VALID_EMAIL}    ${VALID_MESSAGE}    ${VALID_PHONE}    Working Professional
    Submit Contact Form
    Verify Contact Submission Does Not Show Error Message

Contact - Keyboard Navigation Test
    [Tags]    contact
    Navigate To Contact Page
    Press Keys    ${FIELD_NAME}    Tab
    Press Keys    ${FIELD_EMAIL}    Tab
    Press Keys    ${FIELD_PHONE}    Tab
    Press Keys    ${FIELD_MESSAGE}    Tab
    Press Keys    ${FIELD_USER_TYPE}    Tab

# ══════════════════════════════════════════════
# FREE ACCESS TESTS
# ══════════════════════════════════════════════



Free Access - Enter Valid Full Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_FULL_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Leave Full Name Blank
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${EMPTY}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_name

Free Access - Enter Numbers In Full Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${NUMERIC_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_name

Free Access - Enter Special Characters In Full Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${SPECIAL_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_name

Free Access - Enter Maximum Length Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${LONG_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Full Name Leading And Trailing Spaces
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${SPACED_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Enter Valid Email
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Leave Email Blank
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${EMPTY}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_email

Free Access - Invalid Email Format
    [Tags]    free-access
    FOR    ${email}    IN    ${WRONG_FREE_ACCESS_EMAIL_1}    ${WRONG_FREE_ACCESS_EMAIL_2}    ${WRONG_FREE_ACCESS_EMAIL_3}
        Navigate To free-access Page
        Verify Free Access Page Is Loaded
        Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${email}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
        ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
        Submit Free Access Form
        Verify Free Access Validation Error    invalid_email
    END

Free Access - Email With Spaces
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${FREE_ACCESS_EMAIL_WITH_SPACES}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_email

Free Access - Duplicate Email
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${DUPLICATE_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    duplicate_email

Free Access - Email Case Handling
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${FREE_ACCESS_EMAIL_UPPERCASE}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Valid Phone Number
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Blank Phone Number
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${EMPTY}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_phone

Free Access - Phone Less Than 10 Digits
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${INVALID_FREE_ACCESS_PHONE_1}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    invalid_phone

Free Access - Phone More Than 10 Digits
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${INVALID_FREE_ACCESS_PHONE_MORE_DIGITS}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_phone

Free Access - Alphabets In Phone Number
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${INVALID_FREE_ACCESS_PHONE_ALPHABETS}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_phone

Free Access - Special Characters In Phone Number
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${INVALID_FREE_ACCESS_PHONE_SPECIAL}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_phone

Free Access - Verify Country Code
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Country Code Display

Free Access - Open Institution Type Dropdown
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Open Free Access Institution Type Dropdown

Free Access - Select Each Institution Type
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Select Each Free Access Institution Type

Free Access - Leave Institution Type Unselected
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${EMPTY}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_qualification

Free Access - Close Institution Type Dropdown
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Open Free Access Institution Type Dropdown
    Click    ${FIELD_FREE_ACCESS_NAME}
    Verify Free Access Institution Type Dropdown Closed

Free Access - Valid Institution Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Blank Institution Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${EMPTY}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_institute_name

Free Access - Special Characters In Institution Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${SPECIAL_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error Or Stable Form    invalid_institute_name

Free Access - Long Institution Name
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${LONG_FREE_ACCESS_INSTITUTE_NAME_MAX}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Valid Field Of Study
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Blank Field Of Study
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${EMPTY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_field_of_study

Free Access - Numeric Field Of Study
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${NUMERIC_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Long Field Of Study
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${LONG_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Select Valid Graduation Month And Year
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Leave Graduation Month Blank
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${EMPTY}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_month

Free Access - Leave Graduation Year Blank
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${EMPTY}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_year

Free Access - Leave Graduation Month And Year Blank
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${EMPTY}    ${EMPTY}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_graduation_date

Free Access - Verify Year Values
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Year Values

Free Access - Past Graduation Year
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${PAST_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Verify Month Values
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Month Values

Free Access - Upload JPG
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_JPG}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Upload PNG
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PNG}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Upload PDF
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Unsupported File Type
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_UNSUPPORTED_DOCX}
    Verify Free Access Upload Error Or Stable Form    unsupported_resume

Free Access - File Greater Than 5 MB
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_OVERSIZED}
    Verify Free Access Upload Error Or Stable Form    oversized_resume

Free Access - File Exactly 5 MB
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_EXACT_5MB}
    Verify Free Access Upload Stability

Free Access - Corrupted File
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_CORRUPTED}
    Verify Free Access Upload Error Or Stable Form    unsupported_resume

Free Access - Special Characters Filename
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_SPECIAL_FILENAME}
    Verify Free Access Upload Stability

Free Access - Replace Uploaded File
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Replace Free Access Uploaded File    ${RESUME_FREE_ACCESS_VALID_PDF}    ${RESUME_FREE_ACCESS_VALID_PNG}

Free Access - Remove Uploaded File
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Remove Free Access Uploaded File If Available    ${RESUME_FREE_ACCESS_VALID_PDF}

Free Access - Drag And Drop Upload
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Upload Resume File    ${RESUME_FREE_ACCESS_VALID_PDF}
    Verify Free Access Upload Stability

Free Access - Multiple Files
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Replace Free Access Uploaded File    ${RESUME_FREE_ACCESS_VALID_PDF}    ${RESUME_FREE_ACCESS_VALID_JPG}

Free Access - Submit Valid Form
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Submit Empty Form
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Submit Free Access Form
    Verify Free Access Validation Error    empty_name
    Verify Free Access Validation Error    empty_email
    Verify Free Access Validation Error    empty_phone
    Verify Free Access Validation Error    empty_qualification

Free Access - One Mandatory Field Missing
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${EMPTY}
    Submit Free Access Form
    Verify Free Access Validation Error    empty_resume

Free Access - Button State During Submission
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Verify Free Access Submit Button State During Submission

Free Access - Double Click Submit
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Double Click Free Access Submit Button

Free Access - Enter Key Submission
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form By Enter
    Verify Free Access Submission Stability

Free Access - Verify Validation Messages
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Multiple Validation Messages

Free Access - Correct Invalid Data
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Correct Free Access Invalid Email And Submit

Free Access - Network Failure
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Submit Free Access Form While Offline

Free Access - Server Error
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Server Error Handling

Free Access - Session Timeout
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Session Timeout Handling

Free Access - Copy Paste Data
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form With Pasted Data
    Submit Free Access Form
    Verify Free Access Submission Stability

Free Access - Refresh Page
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Refresh Behavior

Free Access - Spaces Only
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Submit Free Access Spaces Only Data

Free Access - Browser Navigation
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Browser Navigation

Free Access - Mobile Responsive
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Mobile Responsive

Free Access - Accessibility
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Keyboard Accessibility

Free Access - Successful Submission Flow
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Verify Free Access Successful Submission Flow

Free Access - Verify Correct Submission Does Not Show Status Error
    [Tags]    free-access
    Navigate To free-access Page
    Verify Free Access Page Is Loaded
    Fill Free Access Form    ${VALID_FREE_ACCESS_FULL_NAME}    ${VALID_FREE_ACCESS_EMAIL}    ${VALID_FREE_ACCESS_PHONE}    ${VALID_FREE_ACCESS_QUALIFICATION}
    ...    ${VALID_FREE_ACCESS_INSTITUTE_NAME}    ${VALID_FREE_ACCESS_FIELD_OF_STUDY}    ${VALID_FREE_ACCESS_MONTH}    ${VALID_FREE_ACCESS_YEAR}    ${RESUME_FREE_ACCESS_VALID_PDF}
    Submit Free Access Form
    Verify Free Access Submission Does Not Show Error Message
# ══════════════════════════════════════════════
# EXPLORE CAREER TESTS
# ══════════════════════════════════════════════

Explore Career - Verify Job Detail Page Loads Successfully
    [Tags]    explore-career
    Open First Job Detail Page

Explore Career - Verify Resume Upload With Valid File
    [Tags]    explore-career
    Open First Job Detail Page
    Upload Job Detail Resume    ${CAREER_RESUME_VALID_PDF}
    Verify Job Detail Resume Upload Stability

Explore Career - Verify Resume Upload With Unsupported File Format
    [Tags]    explore-career
    Open First Job Detail Page
    Upload Job Detail Resume    ${CAREER_RESUME_UNSUPPORTED}
    Verify Job Detail Upload Error Or Stable Form

Explore Career - Verify Resume Upload With File Size Exceeding Limit
    [Tags]    explore-career
    Open First Job Detail Page
    Upload Job Detail Resume    ${CAREER_RESUME_OVERSIZED}
    Verify Job Detail Upload Error Or Stable Form

Explore Career - Verify Apply Button Without Resume Upload
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Job Detail Apply Without Resume

Explore Career - Verify Apply Button After Resume Upload
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Job Detail Apply After Resume Upload

Explore Career - Verify Multiple Similar Job Navigation
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Multiple Similar Job Navigation

Explore Career - Verify Browser Back Navigation From Similar Job
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Browser Back Navigation From Similar Job

Explore Career - Verify No Similar Jobs Available State
    [Tags]    explore-career
    Open First Job Detail Page
    Verify No Similar Jobs Available State

Explore Career - Verify Similar Job Cards Are Clickable
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Similar Job Cards Are Clickable

Explore Career - Verify Similar Job Opens Correct Details
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Similar Job Opens Correct Details

Explore Career - Verify Similar Job Card Opens On Entire Card Click
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Similar Job Card Whole Area Clickable

Explore Career - Verify Refresh On Job Detail Page
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Job Detail Refresh

Explore Career - Verify Direct Job URL Access
    [Tags]    explore-career
    ${job_url}=    Get First Job Detail Url
    Verify Direct Job Url Access    ${job_url}

Explore Career - Verify Invalid Job URL Handling
    [Tags]    explore-career
    Open First Job Detail Page
    Verify Invalid Job Url Handling

Explore Career - Verify No Blank Page During Navigation
    [Tags]    explore-career
    Open First Job Detail Page
    Verify No Blank Page During Navigation

Explore Career - Verify Tab Switching Works Correctly
    [Tags]    explore-career
    Verify Tab Switching Works Correctly

Explore Career - Verify Case-Insensitive Search
    [Tags]    explore-career
    Open Explore Career Page
    Verify Search Results Are Case Insensitive     automation tester

Explore Career - Verify Special Character Search
    [Tags]    explore-career
    Open Explore Career Page
    Verify Special Character Search Handles Input    @#$%

Explore Career - Verify No Result Search
    [Tags]    explore-career
    Open Explore Career Page
    Verify Search Returns No Results    NonExistingKeyword123

Explore Career - Verify Empty Search
    [Tags]    explore-career
    Open Explore Career Page
    Verify Empty Search Shows All Records

Explore Career - Verify Filter Change
    [Tags]    explore-career
    Open Explore Career Page
    Verify Filter Change Updates Results

Explore Career - Sort By Newest
    Open Explore Career Page
    Wait For Elements State    ${SORT_BY_NEWEST}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SORT_BY_NEWEST}

Explore Career - Sort By Highest Paid
    Open Explore Career Page
    Wait For Elements State    ${SORT_BY_HIGHEST}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SORT_BY_HIGHEST}
    Wait For Elements State    ${SEARCH_JOBS_INPUT}    visible

Explore Career - Sort By Trending
    Open Explore Career Page
    Wait For Elements State    ${SORT_BY_TRENDING}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${SORT_BY_TRENDING}


Explore Career - Next Button From First Page
    Open Explore Career Page
    Verify Next Page

Explore Career - Previous Button From Second Page
    Open Explore Career Page
    Wait For Elements State    ${PAGINATION_NEXT_BUTTON}    visible    timeout=${DEFAULT_TIMEOUT}
    Click    ${PAGINATION_NEXT_BUTTON}
    Verify Previous Page



