# driver_factory.py
# Previously created a raw Selenium WebDriver.
# Selenium has been removed in favour of robotframework-browser (Playwright).
# Browser lifecycle is now managed by hopscotch_client.py via the Browser library.
# This file is kept as a stub to avoid ImportError if any legacy reference exists.

def create_driver(headless=True):
    raise NotImplementedError(
        "driver_factory.create_driver() is disabled. "
        "Browser sessions are managed by hopscotch_client.py via robotframework-browser."
    )
