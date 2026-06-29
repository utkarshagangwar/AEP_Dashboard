# resources/variables/config.py
# ─────────────────────────────────────────────────────────────────────────────
# All sensitive values (API keys, URLs, credentials) are read from environment
# variables so nothing secret is ever committed to the repository.
#
# For local runs: copy ..env to .env and fill in real values,
# then load it before running Robot Framework:
#   export $(cat .env | xargs) && robot --argumentfile local.args tests/
# ─────────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# ── Browser ───────────────────────────────────────────────────────────────────
BROWSER         = os.environ.get("BROWSER", "chrome")
Timeout         = os.environ.get("TIMEOUT", "15")
SLEEP           = os.environ.get("SLEEP", "5")

# Viewport — override via VIEWPORT_WIDTH / VIEWPORT_HEIGHT in .env or CI secrets.
# Default is Playwright's own default (1280×720). Do NOT hardcode 1920×1080 here
# because that forces a layout larger than many local monitor resolutions.
VIEWPORT_WIDTH  = int(os.environ.get("VIEWPORT_WIDTH",  "1280"))
VIEWPORT_HEIGHT = int(os.environ.get("VIEWPORT_HEIGHT", "720"))

# ── App URLs ──────────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "https://pre-prod.interviewgod.ai/")

# ── Suite A: CI/CD Bypass Login ───────────────────────────────────────────────
# These values are set as GitHub Actions secrets in CI
# and as environment variables for local runs via .env

# Base URL of the backend API (no trailing slash)
API_BASE_URL = os.environ.get("API_BASE_URL", "")

# Endpoint path for the bypass login
BYPASS_ENDPOINT    = os.environ.get("BYPASS_ENDPOINT", "/admin-login-by-api-key")

# Secret API key — header name is always x-api-key per the confirmed schema
X_API_KEY          = os.environ.get("X_API_KEY", "")

# Cookie name confirmed from browser devtools → Application → Cookies
AUTH_COOKIE_NAME   = os.environ.get("AUTH_COOKIE_NAME", "authToken")

# Domain must match exactly what the browser sees (no https://, no path)
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN", "pre-prod.interviewgod.ai")

# ── Test Account Credentials ──────────────────────────────────────────────────
# Dedicated pre-prod test account — not a real user account
TEST_EMAIL         = os.environ.get("TEST_EMAIL", "")
TEST_OTP           = os.environ.get("TEST_OTP",   "")

# ── Legacy UI login vars (kept for Suite B / login_tests.robot) ───────────────
# These are still used by login_tests.robot which tests the UI login flow
VALID_EMAIL        = os.environ.get("TEST_EMAIL", "test2@interviewgod.ai")
OTP_VALID          = os.environ.get("TEST_OTP",   "123456")
INVALID_EMAIL      = "test@@god"
OTP_INVALID        = "111111"

# ── Job / Candidate test data ─────────────────────────────────────────────────
TYPE                = "Full Time"
Experience          = "2"
DATA_FILE           = "test_data/jobs_data.json"

VALID_CANDIDATE_NAME  = "John Doe"
VALID_CANDIDATE_EMAIL = "johndoe_test@gmail.com"
VALID_CANDIDATE_PHONE = "9876543210"

LONG_NAME             = "A" * 256
SPECIAL_CHARS_NAME    = "!@#$%^&*()"
EMOJI_NAME            = "John 😊 Doe"
SQL_INJECTION         = "'; DROP TABLE candidates; --"
XSS_PAYLOAD           = "<script>alert('xss')</script>"
INVALID_EMAIL_NO_AT   = "invalidemail.com"
INVALID_EMAIL_NO_DOMAIN = "invalid@"
INVALID_EMAIL_SPACES  = "in valid@gmail.com"
INVALID_PHONE_LETTERS = "ABCDE12345"
INVALID_PHONE_SHORT   = "123"
INVALID_PHONE_LONG    = "1" * 20

SCORE_MIN_VALID       = "40"
SCORE_MAX_VALID       = "80"
SCORE_MIN_NEGATIVE    = "-10"
SCORE_MIN_ABOVE_100   = "150"
SCORE_INVALID_TEXT    = "abc"

VALID_PDF_PATH        = "${CURDIR}/../test_files/valid_resume.pdf"
LARGE_PDF_PATH        = "${CURDIR}/../test_files/large_resume_60mb.pdf"
INVALID_FORMAT_PATH   = "${CURDIR}/../test_files/resume.docx"
VALID_EXCEL_PATH      = "${CURDIR}/../test_files/candidates.xlsx"
VALID_XLS_PATH        = "${CURDIR}/../test_files/candidates.xls"
LARGE_EXCEL_PATH      = "${CURDIR}/../test_files/large_data_60mb.xlsx"
INVALID_EXCEL_FORMAT  = "${CURDIR}/../test_files/candidates.csv"
