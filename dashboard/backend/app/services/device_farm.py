"""Cloud device-farm adapter (BrowserStack App Automate) for Android Vibe
Testing runs.

Thin and intentionally not vendor-abstracted: Appium's W3C protocol is
identical across BrowserStack/Sauce Labs/LambdaTest — only three things
differ (hub URL, vendor-options capability namespace, REST endpoints for
app upload / session details). Isolating exactly those three behind the
plain functions below means app.services.android_runner and
app.workers.tasks.ai_execution never talk to a vendor directly; adding a
second vendor later is a branch in this module, not a rewrite elsewhere.

No local Appium server, no Android SDK, no emulator — execution happens
entirely on BrowserStack's hosted devices, reached over HTTPS.
"""
import os
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_UPLOAD_URL = "https://api-cloud.browserstack.com/app-automate/upload"
_SESSION_DETAILS_URL = "https://api-cloud.browserstack.com/app-automate/sessions/{session_id}.json"
_HUB_HOST = "hub-cloud.browserstack.com"

# Small static catalog for MVP — a live farm-catalog fetch is a reasonable
# future enhancement, not needed to get real runs executing.
DEVICE_PROFILES: dict[str, dict] = {
    "pixel_7_android_13": {
        "label": "Google Pixel 7 (Android 13)",
        "deviceName": "Google Pixel 7",
        "platformVersion": "13.0",
    },
    "galaxy_s23_android_13": {
        "label": "Samsung Galaxy S23 (Android 13)",
        "deviceName": "Samsung Galaxy S23",
        "platformVersion": "13.0",
    },
    "pixel_5_android_12": {
        "label": "Google Pixel 5 (Android 12)",
        "deviceName": "Google Pixel 5",
        "platformVersion": "12.0",
    },
}
DEFAULT_DEVICE_PROFILE = "pixel_7_android_13"


class DeviceFarmError(RuntimeError):
    """Raised on any BrowserStack API failure (auth, upload, network)."""


def _credentials() -> tuple[str, str]:
    username = os.environ.get("BROWSERSTACK_USERNAME", "").strip()
    access_key = os.environ.get("BROWSERSTACK_ACCESS_KEY", "").strip()
    if not username or not access_key:
        raise DeviceFarmError(
            "BROWSERSTACK_USERNAME/BROWSERSTACK_ACCESS_KEY are not configured."
        )
    return username, access_key


def upload_apk(file_obj, filename: str) -> dict:
    """Upload an APK/AAB to BrowserStack App Automate's app storage.

    Returns the raw JSON response, e.g. {"app_url": "bs://<hash>",
    "custom_id": None, "shareable_id": "..."}. Raises DeviceFarmError on any
    non-2xx response or network failure — the caller must not create an
    AndroidAppBuild row without a real farm_app_id.
    """
    import requests

    username, access_key = _credentials()
    try:
        resp = requests.post(
            _UPLOAD_URL,
            auth=(username, access_key),
            files={"file": (filename, file_obj)},
            timeout=120,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DeviceFarmError(f"BrowserStack app upload failed: {exc}") from exc

    data = resp.json()
    if not data.get("app_url"):
        raise DeviceFarmError(f"BrowserStack app upload returned no app_url: {data}")
    logger.info("Uploaded APK %s to BrowserStack: %s", filename, data["app_url"])
    return data


def remote_url() -> str:
    """Appium remote hub URL, credentials embedded (Appium-Python-Client's
    webdriver.Remote takes this as command_executor)."""
    username, access_key = _credentials()
    return f"https://{username}:{access_key}@{_HUB_HOST}/wd/hub"


def build_capabilities(
    farm_app_id: str, device_profile: str, session_name: str
) -> dict:
    """W3C capabilities dict for an Appium session against BrowserStack.

    device_profile is a key into DEVICE_PROFILES, not a raw device string —
    keeps the caller decoupled from BrowserStack's exact capability naming.
    """
    profile = DEVICE_PROFILES.get(device_profile) or DEVICE_PROFILES[DEFAULT_DEVICE_PROFILE]
    username, access_key = _credentials()
    new_command_timeout = int(os.environ.get("ANDROID_NEW_COMMAND_TIMEOUT_S", "120"))

    return {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": profile["deviceName"],
        "appium:platformVersion": profile["platformVersion"],
        "appium:app": farm_app_id,
        "appium:newCommandTimeout": new_command_timeout,
        "bstack:options": {
            "userName": username,
            "accessKey": access_key,
            "projectName": "AEP Vibe Testing",
            "buildName": "android-vibe-testing",
            "sessionName": session_name,
            "video": True,
            "debug": True,
        },
    }


def get_session_details(session_id: str) -> Optional[dict]:
    """Fetch {dashboard_url, video_url} for a finished BrowserStack session.

    Returns None (never raises) on any failure — a missing dashboard link
    must never fail run persistence, it's supplementary metadata only.
    """
    import requests

    try:
        username, access_key = _credentials()
        resp = requests.get(
            _SESSION_DETAILS_URL.format(session_id=session_id),
            auth=(username, access_key),
            timeout=15,
        )
        resp.raise_for_status()
        session = resp.json().get("automation_session") or {}
        return {
            "dashboard_url": session.get("public_url"),
            "video_url": session.get("video_url"),
        }
    except Exception:
        logger.exception(
            "Failed to fetch BrowserStack session details for %s", session_id
        )
        return None
