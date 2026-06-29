# base_page.py
# ─────────────────────────────────────────────────────────────────────────────
# Migrated from Selenium to Playwright (robotframework-browser).
#
# Note: BasePage is not currently used by any active page object in this
# project (all page objects use the Browser library via RF keywords directly).
# This file is retained as a base class stub for future use.
# ─────────────────────────────────────────────────────────────────────────────

from robot.libraries.BuiltIn import BuiltIn


class BasePage:
    def __init__(self, timeout=30):
        self.timeout = timeout

    @property
    def browser_lib(self):
        """Accesses the active Browser library instance in Robot Framework."""
        return BuiltIn().get_library_instance("Browser")

    def find(self, locator):
        """Wait for element to be visible and return its handle."""
        self.browser_lib.wait_for_elements_state(locator, "visible", f"{self.timeout}s")

    def click(self, locator):
        """Wait for element to be visible then click it."""
        self.browser_lib.wait_for_elements_state(locator, "visible", f"{self.timeout}s")
        self.browser_lib.click(locator)

    def type(self, locator, value, clear=True):
        """Fill text into a field, optionally clearing first."""
        self.browser_lib.fill_text(locator, value, clear=clear)

    def go_to(self, url):
        """Navigate to a URL."""
        self.browser_lib.go_to(url)
