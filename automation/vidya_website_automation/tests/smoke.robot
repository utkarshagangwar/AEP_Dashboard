*** Settings ***
Resource        ../resources/common/browser.resource
Resource        ../resources/common/navigation.resource
Resource        ../resources/vidya/explore_career.resource
Resource        ../resources/vidya/free_access.resource
Resource        ../resources/vidya/contact.resource
Variables       ../variables/vidya_vars.py

Suite Setup       Open Browser Session
Suite Teardown    Close Browser Session
Test Teardown     Take Screenshot On Fail


*** Test Cases ***

