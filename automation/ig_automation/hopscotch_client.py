"""
hopscotch_client.py
────────────────────────────────────────────────────────────────────────────────
Robot Framework library — Suite A CI/CD auth bypass.

Calls POST /admin-login-by-api-key on the backend,
receives the auth_token, and injects it as a cookie into the
Selenium browser session so tests start already logged in.

All secrets come from environment variables — nothing is hardcoded.
────────────────────────────────────────────────────────────────────────────────
"""

import os
from pathlib import Path
import logging
import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

# FIX: replaced time.sleep with proper Selenium waits
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

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
            "base_url":      os.environ.get("API_BASE_URL", "").rstrip("/"),
            "endpoint":      os.environ.get("BYPASS_ENDPOINT", "/admin-login-by-api-key"),
            "api_key":       os.environ.get("X_API_KEY", ""),
            "cookie_name":   os.environ.get("AUTH_COOKIE_NAME", "authToken"),
            "cookie_domain": os.environ.get("AUTH_COOKIE_DOMAIN", ""),
            "email":         os.environ.get("TEST_EMAIL", ""),
            "otp":           os.environ.get("TEST_OTP", ""),
            "base_app_url":  os.environ.get("BASE_URL", "").rstrip("/"),
            "browser":       os.environ.get("BROWSER", "headlesschrome"),
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

    # FIX: wait for the browser to land on a stable page in the target origin
    def _wait_for_origin(self, driver, cookie_domain: str, timeout: int = 15) -> None:
        """
        Block until the browser's current URL contains the target domain.
        This prevents add_cookie() from firing during a redirect or on about:blank,
        which was the root cause of the cookie being silently rejected.
        """
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: cookie_domain in d.current_url
            )
            logger.info(
                f"[HopscotchClient] Browser settled on: {driver.current_url}"
            )
        except TimeoutException:
            raise RuntimeError(
                f"[HopscotchClient] Browser did not reach '{cookie_domain}' "
                f"within {timeout}s. Last URL: {driver.current_url}. "
                f"Check BASE_URL and network connectivity."
            )

    # FIX: verify the cookie actually exists after add_cookie() to catch silent failures
    def _verify_cookie_injected(self, driver, cookie_name: str) -> None:
        """
        Confirm the cookie was accepted by the browser.
        Raises if the cookie is absent — avoids a misleading 30s timeout.
        """
        cookies = {c["name"]: c for c in driver.get_cookies()}
        if cookie_name not in cookies:
            raise RuntimeError(
                f"[HopscotchClient] Cookie '{cookie_name}' was NOT found after "
                f"add_cookie(). Current cookies: {list(cookies.keys())}. "
                f"Possible cause: browser was not on the correct origin when "
                f"add_cookie() was called."
            )
        logger.info(
            f"[HopscotchClient] Cookie confirmed → "
            f"name={cookies[cookie_name]['name']}  "
            f"domain={cookies[cookie_name].get('domain', 'n/a')}  "
            f"path={cookies[cookie_name].get('path', 'n/a')}"
        )

    # FIX: replaced polling while-loop with WebDriverWait for dashboard arrival
    def _wait_for_dashboard(self, driver, base_app_url: str, timeout: int = 30) -> None:
        """
        Wait until the browser URL contains /dashboard and NOT /login.
        """
        dashboard_url = f"{base_app_url}/dashboard"
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: "/dashboard" in d.current_url and "/login" not in d.current_url
            )
            logger.info(
                f"[HopscotchClient] Session established. URL: {driver.current_url}"
            )
        except TimeoutException:
            raise RuntimeError(
                f"[HopscotchClient] Dashboard did not load after {timeout}s. "
                f"Last URL: {driver.current_url}. "
                f"The cookie was injected but the SPA did not accept it. "
                f"Confirm the auth_token is valid and AUTH_COOKIE_DOMAIN "
                f"exactly matches the browser origin (no https://, no slash). "
                f"Expected dashboard at: {dashboard_url}"
            )

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

        # Step 1: fetch token before opening the browser
        token = self._fetch_token(config, resolved_email, resolved_otp)

        selenium = BuiltIn().get_library_instance("SeleniumLibrary")

        # Step 2: open browser and navigate to base URL
        selenium.open_browser(config["base_app_url"], config["browser"])
        selenium.maximize_browser_window()

        driver = selenium.driver

        # Step 3: FIX — wait for browser to land on the target domain
        # (SPA redirects to /login; that is fine — we just need a stable origin)
        # This replaces the brittle time.sleep(1.0) that caused the race condition.
        self._wait_for_origin(driver, config["cookie_domain"])

        # Step 4: inject auth cookie
        driver.add_cookie({
            "name":  config["cookie_name"],
            "value": token,
            "path":  "/",
            # domain intentionally omitted → host-only cookie,
            # matched to current URL's host by WebDriver
        })

        # Step 5: FIX — verify the cookie was actually accepted
        self._verify_cookie_injected(driver, config["cookie_name"])

        # ── TEMP DEBUG ────────────────────────────────────────────────────────
        all_cookies = driver.get_cookies()
        print(f"[DEBUG] All cookies at injection point: {all_cookies}")
        print(f"[DEBUG] Current URL at injection point: {driver.current_url}")
        # ─────────────────────────────────────────────────────────────────────

        # Step 6: navigate to dashboard — one go_to is enough
        # The SPA will read the cookie on this navigation and skip /login
        selenium.go_to(f"{config['base_app_url']}/dashboard")

        # Step 7: FIX — WebDriverWait replaces the fragile polling while-loop
        self._wait_for_dashboard(driver, config["base_app_url"])

    def refresh_session_token(
        self,
        email: str = "",
        otp:   str = "",
    ) -> None:
        config = self._get_config()

        resolved_email = email or config["email"]
        resolved_otp   = otp   or config["otp"]

        token    = self._fetch_token(config, resolved_email, resolved_otp)
        selenium = BuiltIn().get_library_instance("SeleniumLibrary")
        driver   = selenium.driver

        # FIX: verify origin before touching cookies
        self._wait_for_origin(driver, config["cookie_domain"])

        driver.delete_cookie(config["cookie_name"])
        driver.add_cookie({
            "name":  config["cookie_name"],
            "value": token,
            "path":  "/",
        })

        # FIX: verify the refreshed cookie was accepted
        self._verify_cookie_injected(driver, config["cookie_name"])

        selenium.go_to(f"{config['base_app_url']}/dashboard")
        logger.info("[HopscotchClient] Session token refreshed.")


_CLIENT = HopscotchClient()


def bypass_login_and_open_session(email: str = "", otp: str = "") -> None:
    _CLIENT.bypass_login_and_open_session(email, otp)


def refresh_session_token(email: str = "", otp: str = "") -> None:
    _CLIENT.refresh_session_token(email, otp)

