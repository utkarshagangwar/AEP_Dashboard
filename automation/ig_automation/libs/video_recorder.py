"""
video_recorder.py
────────────────────────────────────────────────────────────────────────────────
Robot Framework library — per-test video recording lifecycle manager.

Folder structure (written):
  test-artifacts/
  └── videos/
      └── {suite-name}/                    ← one folder per test script
          ├── Dashboard_Load.webm
          ├── Dashboard_Title_Is_Overview.webm
          └── ...

All tests from the same .robot file share one folder.
Files inside are named after the individual test case (rolling overwrite per test).

No dependencies beyond stdlib. robotframework-browser handles actual capture;
this library only manages the file-system side.
────────────────────────────────────────────────────────────────────────────────
"""

import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path("test-artifacts")


def _sanitize(name: str) -> str:
    """Replace spaces and filesystem-unsafe characters with underscores."""
    return re.sub(r'[<>:"/\\|?*\n\r\t ]', "_", name).strip("_") or "unnamed"


class VideoRecorder:

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    _last_video_dir:  Path | None = None
    _last_test_name:  str         = ""

    # ── Public RF keyword methods ──────────────────────────────────────────────

    def prepare_video_dir(self, suite_name: str, test_name: str) -> str:
        """
        Create test-artifacts/videos/{suite_name}/ (one folder per test script)
        and cache the absolute path + test name for use by finalize_video.

        Returns an ABSOLUTE POSIX path so Playwright's Node.js process resolves
        to the same directory regardless of its own CWD.

        Call this as the first step in Test Setup, before New Context.
        suite_name → folder name (test script).
        test_name  → video file name inside that folder.
        """
        video_dir = ARTIFACTS_ROOT / "videos" / _sanitize(suite_name)
        video_dir = video_dir.resolve()
        video_dir.mkdir(parents=True, exist_ok=True)

        VideoRecorder._last_video_dir = video_dir
        VideoRecorder._last_test_name = test_name

        logger.info(f"[VideoRecorder] Video dir: {video_dir} | Test: {test_name}")
        return video_dir.as_posix()

    def get_last_video_dir(self) -> str:
        """Return the cached absolute video dir path. Called in Test Teardown."""
        if VideoRecorder._last_video_dir is None:
            raise RuntimeError(
                "[VideoRecorder] No video dir set. "
                "Was Prepare Video Dir called in Test Setup?"
            )
        return str(VideoRecorder._last_video_dir)

    def finalize_video(self, video_dir: str, retries: int = 10, delay: float = 1.0) -> str:
        """
        Find the Playwright-generated .webm file in video_dir (Playwright names it
        like `page@<hash>.webm`) and rename it to <test_name>.webm, overwriting any
        video from a previous run of that test.

        Windows file-lock safety (the WinError 32 fix):
          After Close Context, Playwright's Node.js process may still hold the .webm
          handle for a moment. On Windows the rename/unlink then raises
          PermissionError: [WinError 32] ... used by another process. That used to
          escape this method and fail the test IN TEARDOWN — a false failure on a
          test whose body actually passed.

          This method now (a) retries the rename itself on PermissionError/OSError,
          not just the file search, and (b) NEVER raises — on persistent lock it
          logs a warning and returns "" so teardown can never fail a test.

        Returns the absolute path of the saved video, or empty string on failure.
        """
        target_dir  = Path(video_dir)
        # File is named after the test case — rolling overwrite per test name.
        test_name   = VideoRecorder._last_test_name or "unknown_test"
        target_name = _sanitize(test_name) + ".webm"
        target_path = target_dir / target_name

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                # Any .webm that is not already our finalized target is a fresh
                # Playwright recording (e.g. page@<hash>.webm). Newest = this test.
                candidates = sorted(
                    [f for f in target_dir.glob("*.webm") if f != target_path],
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    newest = candidates[0]
                    if target_path.exists():
                        target_path.unlink()          # rolling overwrite
                    os.replace(newest, target_path)   # atomic on same volume
                    logger.info(f"[VideoRecorder] Saved → {target_path}")
                    return str(target_path)
                logger.warning(
                    f"[VideoRecorder] No .webm found yet "
                    f"(attempt {attempt}/{retries}) — waiting {delay}s..."
                )
            except (PermissionError, OSError) as exc:
                # Node still holds the handle (WinError 32) — wait and retry.
                last_err = exc
                logger.warning(
                    f"[VideoRecorder] Video file still locked "
                    f"(attempt {attempt}/{retries}): {exc}"
                )

            time.sleep(delay)

        logger.error(
            f"[VideoRecorder] Could not finalize video in {video_dir} after "
            f"{retries} attempts. Last error: {last_err}. "
            f"Skipping — teardown will not fail the test for this."
        )
        return ""   # never raise — teardown must not fail the test


# ── Module-level RF keyword functions ─────────────────────────────────────────

_RECORDER = VideoRecorder()


def prepare_video_dir(suite_name: str, test_name: str) -> str:
    return _RECORDER.prepare_video_dir(suite_name, test_name)


def get_last_video_dir() -> str:
    return _RECORDER.get_last_video_dir()


def finalize_video(video_dir: str) -> str:
    return _RECORDER.finalize_video(video_dir)
