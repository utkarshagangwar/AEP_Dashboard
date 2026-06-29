"""
hopscotch_client.py
────────────────────────────────────────────────────────────────────────────────
Robot Framework library — Suite A CI/CD auth bypass.

Calls POST /admin-login-by-api-key on the backend,
receives the auth_token, and injects it as a cookie into the
Playwright Browser library session so tests start already logged in.

All secrets come from environment variables — nothing is hardcoded.

Migration note (Selenium → Playwright):
  - Removed: selenium.webdriver.support.ui.WebDriverWait
  - Removed: selenium.common.exceptions
  - Uses: robotframework-browser (Playwright) library instance
  - Cookie injection and URL polling use the Browser library Python API.
  - The functional flow is IDENTICAL to the Selenium version:
      Step 1: HTTP POST → fetch auth_token
      Step 2: Open browser and navigate to base URL
      Step 3: Wait until browser is on the target origin
      Step 4: Inject authToken cookie
      Step 5: Verify cookie was accepted
      Step 6: Navigate to /dashboard
      Step 7: Wait for dashboard URL to appear
────────────────────────────────────────────────────────────────────────────────
"""

import os
import time
from pathlib import Path
import logging
import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

from robot.libraries.BuiltIn import BuiltIn

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load repo-local .env values when the shell has not exported them."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
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


class HopscotchClient:

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self):
        pass

    def _get_config(self) -> dict:
        config = {
            "base_url":       os.environ.get("API_BASE_URL", "").rstrip("/"),
            "endpoint":       os.environ.get("BYPASS_ENDPOINT", "/admin-login-by-api-key"),
            "api_key":        os.environ.get("X_API_KEY", ""),
            "cookie_name":    os.environ.get("AUTH_COOKIE_NAME", "authToken"),
            "cookie_domain":  os.environ.get("AUTH_COOKIE_DOMAIN", ""),
            "email":          os.environ.get("TEST_EMAIL", ""),
            "otp":            os.environ.get("TEST_OTP", ""),
            "base_app_url":   os.environ.get("BASE_URL", "").rstrip("/"),
            "browser":        os.environ.get("BROWSER", "headlesschrome"),
            # Viewport — reads from env var so local and CI can differ.
            # Defaults to 1280×720 (Playwright's own default).
            "viewport_width":  int(os.environ.get("VIEWPORT_WIDTH",  "1280")),
            "viewport_height": int(os.environ.get("VIEWPORT_HEIGHT", "720")),
        }
        missing = [k for k in ["base_url", "api_key", "cookie_domain"] if not config[k]]
        if missing:
            env_names = {
                "base_url":      "API_BASE_URL",
                "api_key":       "X_API_KEY",
                "cookie_domain": "AUTH_COOKIE_DOMAIN",
            }
            raise RuntimeError(
                f"[HopscotchClient] Missing required environment variables: "
                f"{', '.join(env_names[k] for k in missing)}"
            )
        return config

    def _fetch_token(self, config: dict, email: str, otp: str) -> str:
        """Make POST /admin-login-by-api-key and return the auth_token string."""
        url = f"{config['base_url']}{config['endpoint']}"

        headers = {
            "Content-Type": "application/json",
            "x-api-key":    config["api_key"],
        }
        body = {
            "email": email,
            "otp":   otp,
        }

        logger.info(f"[HopscotchClient] POST {url}")

        try:
            response = requests.post(
                url,
                json=body,
                headers=headers,
                timeout=15,
                verify=True,
            )
        except ConnectionError as exc:
            raise RuntimeError(
                f"[HopscotchClient] Cannot reach {url}. "
                f"Check API_BASE_URL and network. Detail: {exc}"
            )
        except Timeout:
            raise RuntimeError(
                "[HopscotchClient] Request timed out after 15s."
            )
        except RequestException as exc:
            raise RuntimeError(
                f"[HopscotchClient] Unexpected request error: {exc}"
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"[HopscotchClient] HTTP {response.status_code}. "
                f"Body: {response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(
                f"[HopscotchClient] Response is not valid JSON. "
                f"Raw: {response.text[:300]}"
            )

        token = data.get("auth_token")
        if not token:
            raise RuntimeError(
                f"[HopscotchClient] 'auth_token' not found in response. "
                f"Keys present: {list(data.keys())}. "
                f"Full response: {data}"
            )

        # ── TEMP DEBUG: remove after confirming token ────────────────────────
        logger.info(f"[HopscotchClient] Token received (first 20 chars): {token[:20]}...")
        print(f"[DEBUG] Token received (first 20 chars): {token[:20]}...")
        # ─────────────────────────────────────────────────────────────────────

        logger.info("[HopscotchClient] auth_token received successfully.")
        return token

    # ── Browser helpers (Playwright / Browser library) ─────────────────────

    def _resolve_browser_args(self, browser_raw: str) -> tuple:
        """
        Map a Selenium-style browser string to (playwright_browser_type, headless).
        Supported inputs: chrome, headlesschrome, chromium, firefox, headlessfirefox, webkit.
        """
        raw = browser_raw.lower()
        headless = "headless" in raw

        if "firefox" in raw:
            btype = "firefox"
        elif "webkit" in raw or "safari" in raw:
            btype = "webkit"
        else:
            btype = "chromium"   # default: chrome / headlesschrome → chromium

        return btype, headless

    def _wait_for_origin(self, browser_lib, cookie_domain: str, timeout: int = 15) -> None:
        """
        Poll until the browser's current URL contains the target domain.
        Replaces the Selenium WebDriverWait lambda from the original code.
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                url = browser_lib.get_url()
                if cookie_domain in url:
                    logger.info(
                        f"[HopscotchClient] Browser settled on: {url}"
                    )
                    return
            except Exception:
                pass  # browser may still be loading — keep polling
            time.sleep(0.5)

        # Time expired — get last URL for the error message
        try:
            last_url = browser_lib.get_url()
        except Exception:
            last_url = "unknown"

        raise RuntimeError(
            f"[HopscotchClient] Browser did not reach '{cookie_domain}' "
            f"within {timeout}s. Last URL: {last_url}. "
            f"Check BASE_URL and network connectivity."
        )

    def _verify_cookie_injected(self, browser_lib, cookie_name: str) -> None:
        """
        Confirm the cookie was accepted by the browser context.
        Raises if the cookie is absent — avoids a misleading 30s timeout.
        Replaces the Selenium driver.get_cookies() check from the original code.
        """
        try:
            cookies = browser_lib.get_cookies() or []
        except Exception as exc:
            raise RuntimeError(
                f"[HopscotchClient] get_cookies() failed: {exc}"
            )

        # Normalise: cookies may be dicts or objects with a 'name' attribute
        def _get_name(c):
            if isinstance(c, dict):
                return c.get("name", "")
            return getattr(c, "name", "")

        cookie_names = [_get_name(c) for c in cookies]

        if cookie_name not in cookie_names:
            raise RuntimeError(
                f"[HopscotchClient] Cookie '{cookie_name}' was NOT found after "
                f"add_cookie(). Current cookies: {cookie_names}. "
                f"Possible cause: browser was not on the correct origin when "
                f"add_cookie() was called."
            )

        logger.info(f"[HopscotchClient] Cookie confirmed → name={cookie_name}")

    def _wait_for_dashboard(self, browser_lib, base_app_url: str, timeout: int = 30) -> None:
        """
        Poll until the browser URL contains /dashboard and NOT /login.
        Replaces the Selenium WebDriverWait lambda from the original code.
        """
        dashboard_url = f"{base_app_url}/dashboard"
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                url = browser_lib.get_url()
                if "/dashboard" in url and "/login" not in url:
                    logger.info(
                        f"[HopscotchClient] Session established. URL: {url}"
                    )
                    return
            except Exception:
                pass
            time.sleep(0.5)

        try:
            last_url = browser_lib.get_url()
        except Exception:
            last_url = "unknown"

        raise RuntimeError(
            f"[HopscotchClient] Dashboard did not load after {timeout}s. "
            f"Last URL: {last_url}. "
            f"The cookie was injected but the SPA did not accept it. "
            f"Confirm the auth_token is valid and AUTH_COOKIE_DOMAIN "
            f"exactly matches the browser origin (no https://, no slash). "
            f"Expected dashboard at: {dashboard_url}"
        )

    # ── Class-level token + config cache (shared across Suite/Test Setup) ────

    _last_token:   str  = ""
    _config_cache: dict = {}

    # ── Public RF keywords ─────────────────────────────────────────────────

    def open_browser_only(self) -> None:
        """
        Suite Setup step for the per-test-video architecture.

        Fetches the auth token from the API and opens the Playwright browser.
        Does NOT open a Context or Page — those are opened per-test by
        inject_stored_token() (called from video_keywords.resource) so each
        test gets its own Playwright context and therefore its own video file.

        Token and config are cached as class variables so inject_stored_token()
        can reuse them without a second HTTP call per test.
        """
        config = self._get_config()
        HopscotchClient._config_cache = config

        token = self._fetch_token(config, config["email"], config["otp"])
        HopscotchClient._last_token = token

        btype, headless = self._resolve_browser_args(config["browser"])
        logger.info(
            f"[HopscotchClient] Opening browser: {btype} (headless={headless})"
        )
        BuiltIn().run_keyword("New Browser", btype, headless)
        logger.info(
            "[HopscotchClient] Browser open. Auth token cached for test-level injection."
        )

    def inject_stored_token(self) -> None:
        """
        Test Setup step — injects the auth token cached by open_browser_only()
        into the current Playwright context (opened with recordVideo by
        video_keywords.resource before this method is called).

        Flow:
          1. Wait for the page to settle on the target origin
          2. Add the authToken cookie to the current context
          3. Verify the cookie was accepted
          4. Navigate to /dashboard and confirm the session is live
        """
        config = HopscotchClient._config_cache
        token  = HopscotchClient._last_token

        if not token:
            raise RuntimeError(
                "[HopscotchClient] No cached auth token. "
                "Was Open Browser Only called in Suite Setup?"
            )
        if not config:
            raise RuntimeError(
                "[HopscotchClient] No cached config. "
                "Was Open Browser Only called in Suite Setup?"
            )

        browser_lib = BuiltIn().get_library_instance("Browser")

        self._wait_for_origin(browser_lib, config["cookie_domain"])

        browser_lib.add_cookie(
            name=config["cookie_name"],
            value=token,
            domain=config["cookie_domain"],
            path="/",
        )
        self._verify_cookie_injected(browser_lib, config["cookie_name"])

        browser_lib.go_to(f"{config['base_app_url']}/dashboard")
        self._wait_for_dashboard(browser_lib, config["base_app_url"])

    def bypass_login_and_open_session(
        self,
        email: str = "",
        otp:   str = "",
    ) -> None:
        config = self._get_config()

        resolved_email = email or config["email"]
        resolved_otp   = otp   or config["otp"]

        if not resolved_email or not resolved_otp:
            raise RuntimeError(
                "[HopscotchClient] Email and OTP required. "
                "Set TEST_EMAIL and TEST_OTP in environment."
            )

        # Step 1: fetch token before opening the browser (unchanged)
        token = self._fetch_token(config, resolved_email, resolved_otp)

        # Step 2: get Browser library instance (replaces SeleniumLibrary)
        browser_lib = BuiltIn().get_library_instance("Browser")

        # Step 3: open browser and navigate to base URL
        btype, headless = self._resolve_browser_args(config["browser"])
        logger.info(
            f"[HopscotchClient] Opening {btype} (headless={headless}) → "
            f"{config['base_app_url']}"
        )
        # Use run_keyword so RF's type-conversion layer handles
        # the string → SupportedBrowsers enum mapping automatically.
        # Calling the Python API directly bypasses that and errors with
        # "'str' object has no attribute 'name'".
        # NOTE: run_keyword only supports 2 positional args for New Browser
        # (browser, headless). Extra args like --start-maximized cannot be
        # passed positionally — window size is handled via Set Viewport Size.
        BuiltIn().run_keyword("New Browser", btype, headless)
        BuiltIn().run_keyword("New Context")
        # Raise navigation timeout before page load — RF Browser's built-in
        # default is 10 s, which the SPA regularly exceeds on first load.
        BuiltIn().run_keyword("Set Browser Timeout", "30s")
        BuiltIn().run_keyword("New Page", config["base_app_url"])
        # Viewport size is configurable via VIEWPORT_WIDTH / VIEWPORT_HEIGHT env vars.
        # Defaults to 1280×720 — do not hardcode 1920×1080 as it forces a layout
        # larger than the user's actual screen resolution.
        BuiltIn().run_keyword(
            "Set Viewport Size",
            config["viewport_width"],
            config["viewport_height"],
        )

        # Step 4: wait for browser to land on the target domain
        # (SPA redirects to /login; that is fine — we just need a stable origin)
        self._wait_for_origin(browser_lib, config["cookie_domain"])

        # Step 5: inject auth cookie
        # Playwright requires EITHER a url OR both domain+path.
        # Passing only path="/" without domain causes:
        # "Cookie should have a url or a domain/path pair"
        browser_lib.add_cookie(
            name=config["cookie_name"],
            value=token,
            domain=config["cookie_domain"],
            path="/",
        )

        # Step 6: verify the cookie was actually accepted
        self._verify_cookie_injected(browser_lib, config["cookie_name"])

        # ── TEMP DEBUG ────────────────────────────────────────────────────────
        try:
            all_cookies = browser_lib.get_cookies()
            print(f"[DEBUG] All cookies at injection point: {all_cookies}")
            print(f"[DEBUG] Current URL at injection point: {browser_lib.get_url()}")
        except Exception as dbg_exc:
            print(f"[DEBUG] Could not read cookies/url for debug: {dbg_exc}")
        # ─────────────────────────────────────────────────────────────────────

        # Step 7: navigate to dashboard — one go_to is enough
        # The SPA will read the cookie on this navigation and skip /login
        browser_lib.go_to(f"{config['base_app_url']}/dashboard")

        # Step 8: wait for dashboard URL to confirm the session is live
        self._wait_for_dashboard(browser_lib, config["base_app_url"])

    def refresh_session_token(
        self,
        email: str = "",
        otp:   str = "",
    ) -> None:
        config = self._get_config()

        resolved_email = email or config["email"]
        resolved_otp   = otp   or config["otp"]

        # Fetch a fresh token
        token = self._fetch_token(config, resolved_email, resolved_otp)

        browser_lib = BuiltIn().get_library_instance("Browser")

        # Wait for origin before touching cookies
        self._wait_for_origin(browser_lib, config["cookie_domain"])

        # Replace old cookie with fresh one
        try:
            browser_lib.delete_cookie(name=config["cookie_name"])
        except Exception:
            pass  # cookie may not exist yet on first call — safe to ignore

        browser_lib.add_cookie(
            name=config["cookie_name"],
            value=token,
            domain=config["cookie_domain"],
            path="/",
        )

        # Verify the refreshed cookie was accepted
        self._verify_cookie_injected(browser_lib, config["cookie_name"])

        browser_lib.go_to(f"{config['base_app_url']}/dashboard")
        logger.info("[HopscotchClient] Session token refreshed.")


# ── Module-level functions (RF keywords) ──────────────────────────────────────

_CLIENT = HopscotchClient()


def bypass_login_and_open_session(email: str = "", otp: str = "") -> None:
    _CLIENT.bypass_login_and_open_session(email, otp)


def refresh_session_token(email: str = "", otp: str = "") -> None:
    _CLIENT.refresh_session_token(email, otp)


# ── Per-test-video architecture (new) ─────────────────────────────────────────

def open_browser_only() -> None:
    """Suite Setup — fetch token and open browser. Context opened per-test."""
    _CLIENT.open_browser_only()


def inject_stored_token() -> None:
    """Test Setup — inject cached token into the current context's page."""
    _CLIENT.inject_stored_token()
