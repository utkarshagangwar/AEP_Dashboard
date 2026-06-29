"""
evidence_capture.py
────────────────────────────────────────────────────────────────────────────────
Robot Framework library — per-test HTML evidence capture.

Folder structure (written):
  test-artifacts/
  └── html/
      └── {suite-name}/                    ← one folder per test script
          ├── Dashboard_Load.html
          ├── Dashboard_Load.png
          ├── Dashboard_Title_Is_Overview.html
          ├── Dashboard_Title_Is_Overview.png
          └── ...

suite_name → folder (test script name, e.g. "Dashboard Tests" → "Dashboard_Tests")
test_name  → file name inside that folder

Always call BEFORE Close Context — the page is gone after context close.

Screenshot strategy (Windows-safe):
  Browser library's Take Screenshot cannot receive an absolute Windows path —
  its Node.js process strips colons and separators, producing a garbled filename.
  Fix: pass a plain temp name, let Browser save to its default dir, then
  shutil.copy2 the file to our evidence folder.
────────────────────────────────────────────────────────────────────────────────
"""

import logging
import re
import shutil
from pathlib import Path

from robot.libraries.BuiltIn import BuiltIn

logger = logging.getLogger(__name__)

# Absolute path so evidence is always written to the project root's
# test-artifacts/ folder regardless of what the working directory is when
# Robot Framework invokes the library.
ARTIFACTS_ROOT = Path(__file__).resolve().parents[1] / "test-artifacts"


def _sanitize(name: str) -> str:
    """Replace spaces and filesystem-unsafe characters with underscores."""
    return re.sub(r'[<>:"/\\|?*\n\r\t ]', "_", name).strip("_") or "unnamed"


def _is_no_page_error(exc: Exception) -> bool:
    """Return True if the error is the expected 'no page open' condition."""
    s = str(exc).lower()
    return any(phrase in s for phrase in ("no page", "no open page", "no context", "no browser"))


class EvidenceCapture:

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    # ── Public RF keyword methods ──────────────────────────────────────────────

    def capture_html_snapshot(self, suite_name: str, test_name: str) -> str:
        """
        RF keyword: Capture Html Snapshot
        Captures current page HTML and saves to:
          test-artifacts/html/{suite_name}/{test_name}.html

        Returns absolute path to the saved file, or empty string on failure.
        Logs at WARNING (not ERROR) so it never appears as a test error.
        """
        html_dir = ARTIFACTS_ROOT / "html" / _sanitize(suite_name)
        html_dir.mkdir(parents=True, exist_ok=True)

        try:
            html = BuiltIn().run_keyword("Get Page Source")
        except Exception as exc:
            if _is_no_page_error(exc):
                logger.warning("[EvidenceCapture] No page open — HTML snapshot skipped.")
            else:
                logger.warning(f"[EvidenceCapture] HTML snapshot failed: {exc}")
            return ""

        if not html:
            logger.warning("[EvidenceCapture] Page source returned empty.")
            return ""

        file_path = html_dir / f"{_sanitize(test_name)}.html"
        file_path.write_text(html, encoding="utf-8")
        logger.info(f"[EvidenceCapture] HTML saved → {file_path}")
        return str(file_path.resolve())

    def capture_screenshot_evidence(self, suite_name: str, test_name: str) -> str:
        """
        RF keyword: Capture Screenshot Evidence
        Saves screenshot to:
          test-artifacts/html/{suite_name}/{test_name}.png

        Strategy: pass a plain temp name to Take Screenshot (no path separators,
        no drive letter colon). Browser library saves it to
        {outputdir}/browser/screenshot/{temp_name}.png and returns the full path.
        We then shutil.copy2 it to our evidence folder.

        Returns our evidence path, or empty string on failure.
        Logs at WARNING so it never appears as a test error.
        """
        html_dir = ARTIFACTS_ROOT / "html" / _sanitize(suite_name)
        html_dir.mkdir(parents=True, exist_ok=True)
        target_path = html_dir / f"{_sanitize(test_name)}.png"

        # Safe temp name: no drive letter, no path separators, no colons
        temp_name = f"ev_{_sanitize(suite_name)}_{_sanitize(test_name)}"

        try:
            # Browser library saves to its default dir and returns the absolute path
            saved = BuiltIn().run_keyword("Take Screenshot", temp_name)
            saved_path = Path(str(saved))

            if saved_path.exists():
                shutil.copy2(saved_path, target_path)
                logger.info(f"[EvidenceCapture] Screenshot saved → {target_path}")
                return str(target_path)

            # Returned value may be embedded HTML (starts with <img) — ignore gracefully
            if str(saved).strip().startswith("<"):
                logger.warning("[EvidenceCapture] Take Screenshot returned embedded HTML — skipping copy.")
            else:
                logger.warning(f"[EvidenceCapture] Screenshot file not found at: {saved_path}")
            return ""

        except Exception as exc:
            if _is_no_page_error(exc):
                logger.warning("[EvidenceCapture] No page open — screenshot skipped.")
            else:
                logger.warning(f"[EvidenceCapture] Screenshot failed: {exc}")
            return ""

    def capture_test_evidence(self, suite_name: str, test_name: str) -> dict:
        """
        RF keyword: Capture Test Evidence
        Captures both HTML and screenshot. Always call BEFORE Close Context.
        Returns dict with keys 'html' and 'screenshot' (paths or empty strings).
        """
        html_path       = self.capture_html_snapshot(suite_name, test_name)
        screenshot_path = self.capture_screenshot_evidence(suite_name, test_name)
        return {"html": html_path, "screenshot": screenshot_path}

    def get_html_path(self, suite_name: str, test_name: str) -> str:
        """
        RF keyword: Get Html Path
        Returns the expected path to the HTML snapshot for a given test.
        """
        return str(
            (ARTIFACTS_ROOT / "html" / _sanitize(suite_name) / f"{_sanitize(test_name)}.html")
            .resolve()
        )


# ── Module-level RF keyword functions ─────────────────────────────────────────

_CAPTURE = EvidenceCapture()


def capture_html_snapshot(suite_name: str, test_name: str) -> str:
    return _CAPTURE.capture_html_snapshot(suite_name, test_name)


def capture_screenshot_evidence(suite_name: str, test_name: str) -> str:
    return _CAPTURE.capture_screenshot_evidence(suite_name, test_name)


def capture_test_evidence(suite_name: str, test_name: str) -> dict:
    return _CAPTURE.capture_test_evidence(suite_name, test_name)


def get_html_path(suite_name: str, test_name: str) -> str:
    return _CAPTURE.get_html_path(suite_name, test_name)
