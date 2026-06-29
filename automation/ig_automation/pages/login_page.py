from robot.libraries.BuiltIn import BuiltIn

# --- 1. Global Locators (Directly visible to .robot files) ---
LOGIN_LINK     = "xpath=//a[@href='/login' or contains(., 'Login')]"
EMAIL_INPUT    = "xpath=//input[contains(@type,'text') or contains(@placeholder,'Work email address')]"
CONTINUE_BTN   = "xpath=//button[contains(@class,'bg-black') or contains(.,'Continue')]"
OTP_INPUT      = "xpath=//input[@type='text' or @type='number' or contains(@placeholder,'Code')]"
WORKSPACE_NAME = "xpath=//div[contains(@class,'rounded-lg')][.//div[contains(.,'members')]]"
DASHBOARD_NAME = "xpath=//p[contains(@class,'text-neutral-400') and contains(.,'Welcome back!')]"
ERROR_EMAIL    = "xpath=//p[contains(@class,'text-red-500') and contains(text(),'Invalid')]"
ERROR_OTP      = "xpath=//p[contains(@class,'text-red-500') and contains(text(),'OTP')]"
RESEND_BTN     = "xpath=//*[contains(text(),'Resend')]"

class LoginPage:
    ROBOT_LIBRARY_SCOPE = 'GLOBAL'

    def __init__(self):
        # --- 2. Map Globals to Class Attributes ---
        self.RESEND_BTN = RESEND_BTN
        self.ERROR_OTP = ERROR_OTP
        self.ERROR_EMAIL = ERROR_EMAIL
        self.DASHBOARD_NAME = DASHBOARD_NAME
        self.WORKSPACE_NAME = WORKSPACE_NAME
        self.LOGIN_LINK = LOGIN_LINK
        self.OTP_INPUT = OTP_INPUT
        self.CONTINUE_BTN = CONTINUE_BTN
        self.EMAIL_INPUT = EMAIL_INPUT

    @property
    def browser_lib(self):
        """Accesses the active Browser library instance in Robot Framework."""
        return BuiltIn().get_library_instance("Browser")

    # --- 3. Action Methods (Keywords) ---

    def enter_email(self, email):
        self.type(self.EMAIL_INPUT, email)

    def click_continue(self):
        self.click(self.CONTINUE_BTN)

    def enter_otp(self, otp):
        self.type(self.OTP_INPUT, otp)

    def submit_otp(self):
        self.click(self.CONTINUE_BTN)

    def open_login(self):
        self.click(self.LOGIN_LINK)

    def workspace_name(self):
        self.click(self.WORKSPACE_NAME)

    def dashboard_name(self):
        self.click(self.DASHBOARD_NAME)

    def email_error(self):
        self.click(self.ERROR_EMAIL)

    def otp_error(self):
        self.click(self.ERROR_OTP)

    def resend_otp(self):
        self.click(self.RESEND_BTN)

    # --- 4. Helper Methods linked to Browser library ---

    def type(self, locator, text):
        """Uses Browser library to input text."""
        self.browser_lib.wait_for_elements_state(locator, "visible", "15s")
        self.browser_lib.fill_text(locator, text)

    def click(self, locator):
        """Uses Browser library to click element."""
        self.browser_lib.wait_for_elements_state(locator, "visible", "15s")
        self.browser_lib.click(locator)
