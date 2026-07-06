"""Celery task — execute a Visual Audit run (Phase 2).

Flow: load run → capture live screenshot (Playwright, own headless browser,
no CDP port conflict with ai_runner's reserved 9222) → judge (pixel-diff +
vision via router) → persist findings.

Reliability rules:
  * Any provider outage degrades to a deterministic-only "partial" run,
    never a crash.
  * Every failure path writes an error_message and terminal status so the
    UI never shows a run stuck in "running".
"""
import os
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_VIEWPORT = {"width": 1530, "height": 820}  # match ai_runner desktop viewport


def data_dir() -> str:
    """Root folder for Visual QA images (references, screenshots, diffs)."""
    path = os.environ.get("VISUAL_DATA_DIR", os.path.join(os.getcwd(), "visual_qa_data"))
    os.makedirs(path, exist_ok=True)
    return path


def _capture_screenshot(url: str, output_path: str, timeout_ms: int = 60000) -> None:
    """Capture a full-viewport screenshot with Playwright's own Chromium.

    Waits for network idle plus a short settle delay to kill flaky diffs from
    late-loading fonts/images.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)  # settle animations/fonts
            page.screenshot(path=output_path, full_page=False)
        finally:
            browser.close()


@celery_app.task(
    name="visual_audit.run_visual_audit_task",
    bind=True,
    max_retries=0,
)
def run_visual_audit_task(self, run_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import (
        DesignArtifact,
        VisualFinding,
        VisualRun,
        VisualRunStatus,
    )
    from app.services import visual_judge

    session = SessionLocal()
    try:
        run = session.query(VisualRun).filter(VisualRun.id == run_id).one_or_none()
        if run is None:
            logger.error("Visual audit: run %s not found", run_id)
            return
        if run.status == VisualRunStatus.cancelled:
            logger.info("Visual audit: run %s cancelled before start", run_id)
            return

        artifact = (
            session.query(DesignArtifact)
            .filter(DesignArtifact.id == run.artifact_id)
            .one_or_none()
        )
        if artifact is None or not os.path.exists(artifact.storage_path):
            run.status = VisualRunStatus.error
            run.error_message = "Reference design artifact missing on disk."
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        run.status = VisualRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        session.commit()

        run_dir = os.path.join(data_dir(), "runs", str(run.id))
        os.makedirs(run_dir, exist_ok=True)
        screenshot_path = os.path.join(run_dir, "screenshot.png")
        diff_path = os.path.join(run_dir, "diff.png")

        # ── Capture ─────────────────────────────────────────────────────────
        try:
            _capture_screenshot(run.target_url, screenshot_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Visual audit: screenshot capture failed for %s", run_id)
            run.status = VisualRunStatus.error
            run.error_message = f"Screenshot capture failed: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        # ── Judge ───────────────────────────────────────────────────────────
        try:
            verdict = visual_judge.judge(artifact.storage_path, screenshot_path, diff_path)
        except Exception as exc:  # noqa: BLE001 — even pixel-diff failure must terminate cleanly
            logger.exception("Visual audit: judge failed for %s", run_id)
            run.status = VisualRunStatus.error
            run.error_message = f"Judge failed: {exc}"
            run.screenshot_path = screenshot_path
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        # ── Persist ─────────────────────────────────────────────────────────
        for f in verdict.findings:
            session.add(VisualFinding(run_id=run.id, **f))

        fail_pct = float(os.environ.get("VISUAL_FAIL_MISMATCH_PCT", 1.0))
        has_serious = any(
            f["severity"] in ("critical", "major") for f in verdict.findings
        )
        if verdict.pixel_mismatch_pct > fail_pct or has_serious:
            run.status = VisualRunStatus.failed
        elif not verdict.vision_available and not verdict.vision_skipped:
            # Vision pass genuinely unavailable (provider outage) -> partial.
            # A self-execution skip (verdict.vision_skipped) means the Brain
            # deliberately decided pixel-diff alone was conclusive enough —
            # that's a confident result, not a degraded one.
            run.status = VisualRunStatus.partial
        else:
            run.status = VisualRunStatus.passed

        run.screenshot_path = screenshot_path
        run.diff_image_path = verdict.diff_image_path
        run.pixel_mismatch_pct = int(round(verdict.pixel_mismatch_pct))
        run.summary = verdict.summary
        run.completed_at = datetime.now(timezone.utc)
        if run.started_at:
            started = run.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            run.duration_ms = int(
                (run.completed_at - started).total_seconds() * 1000
            )
        session.commit()
        logger.info(
            "Visual audit %s finished: %s (%s%% mismatch, %d findings)",
            run_id,
            run.status.value,
            verdict.pixel_mismatch_pct,
            len(verdict.findings),
        )
    except Exception:
        logger.exception("Visual audit: unexpected failure for %s", run_id)
        session.rollback()
        try:
            run = session.query(VisualRun).filter(VisualRun.id == run_id).one_or_none()
            if run and run.status in (VisualRunStatus.pending, VisualRunStatus.running):
                run.status = VisualRunStatus.error
                run.error_message = "Unexpected worker failure — see worker logs."
                run.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Visual audit: could not mark run %s as errored", run_id)
    finally:
        session.close()
