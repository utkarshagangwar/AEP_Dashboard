*** Settings ***
Documentation    AI-approved locator overrides — audit trail.
...
...              This file is auto-maintained by the HITL manager:
...                python libs/hitl_manager.py apply
...
...              DO NOT edit manually. Each block below is one approved
...              locator change, keyed by page object and attribute name.
...
...              To use an approved locator in a keyword file, import this
...              resource and reference the variable:
...                Resource    ../../resources/locators.resource
...                # Then: ${LOCATOR_DASHBOARDPAGE_TOTAL_CANDIDATES}
...
...              Locator priority (CLAUDE.md §7.1):
...                data-testid > id > CSS > relative XPath


*** Variables ***
# ── No approved locators yet. ────────────────────────────────────────────────
# Run tests, review test-artifacts/ai_suggestions/, approve suggestions,
# then run: python libs/hitl_manager.py apply
