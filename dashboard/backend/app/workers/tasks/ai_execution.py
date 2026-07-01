"""Celery task — execute an AI test run and persist events to the database."""
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _upsert_ai_run_event(session, run_id: str, event_data: dict) -> None:
    """Insert or update a single AIRunEvent row, keyed by (run_id, sequence).

    Used both for live streaming during execution (one throwaway session per
    call, via _make_live_event_sink) and for the post-run reconciliation pass
    below, which fills in any event that failed to persist live (e.g. a
    transient DB hiccup) without creating duplicate rows.
    """
    from app.models.ai_runs import AIRunEvent

    existing = (
        session.query(AIRunEvent)
        .filter(AIRunEvent.run_id == run_id, AIRunEvent.sequence == event_data["sequence"])
        .one_or_none()
    )
    fields = dict(
        status=event_data["status"],
        description=event_data["description"],
        step_type=event_data.get("step_type", "deterministic"),
        elapsed_ms=event_data.get("elapsed_ms"),
        screenshot_url=event_data.get("screenshot_url"),
        highlighted_element=event_data.get("highlighted_element"),
        is_failing_step=event_data.get("is_failing_step", False),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        session.add(AIRunEvent(run_id=run_id, sequence=event_data["sequence"], **fields))


def _make_live_event_sink(run_id: str):
    """Build a callback that persists one AI run event immediately.

    The SSE endpoint (GET /ai-testing/runs/{run_id}/stream) polls the DB
    every 500ms for events with sequence > last_seen. Previously all events
    were written in one batch after the whole run finished, so the "live"
    stream had nothing to show until the very end. This writes each event
    as soon as it happens instead, using a short-lived session per call so
    we don't hold a DB connection open for the whole (potentially long)
    browser automation.
    """

    def _sink(event_data: dict) -> None:
        from sqlalchemy import func

        from app.core.database import SessionLocal
        from app.models.ai_runs import AIRunEvent, AITestRun

        session = SessionLocal()
        try:
            _upsert_ai_run_event(session, run_id, event_data)
            run = session.get(AITestRun, run_id)
            if run is not None:
                max_seq = (
                    session.query(func.max(AIRunEvent.sequence))
                    .filter(AIRunEvent.run_id == run_id)
                    .scalar()
                    or 0
                )
                run.step_count = max_seq
            session.commit()
        except Exception:
            logger.exception(
                "Failed to persist live AI run event (run_id=%s, sequence=%s)",
                run_id,
                event_data.get("sequence"),
            )
            session.rollback()
        finally:
            session.close()

    return _sink


@celery_app.task(
    name="ai_execution.run_ai_test_task",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_ai_test_task(self, run_id: str) -> None:
    """Execute an AI test run identified by run_id."""
    from app.core.database import SessionLocal
    from app.models.ai_runs import (
        AICredentialProfile,
        AIRunStatus,
        AITestRun,
    )

    db = SessionLocal()
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            logger.error("AI run %s not found in DB", run_id)
            return

        # Abort if already cancelled (client cancelled before task started)
        if run.status == AIRunStatus.cancelled:
            logger.info("AI run %s was cancelled before execution started", run_id)
            return

        run.status = AIRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        # Resolve environment URL (best-effort from project record)
        environment_url = "about:blank"
        if run.project_id:
            from app.models.project import Project
            proj = db.get(Project, run.project_id)
            # Project model has no base_url — environment_url stays as placeholder.
            # The AI agent will navigate to the correct URL from the goal text.

        # Resolve credential profile
        allowed_domains: list[str] | None = None
        sensitive_data: dict | None = None
        if run.credential_profile_id:
            profile = db.get(AICredentialProfile, run.credential_profile_id)
            if profile:
                allowed_domains = profile.allowed_domains or []
                if profile.credentials_json:
                    try:
                        from app.services.credential_service import decrypt_credentials
                        sensitive_data = decrypt_credentials(profile.credentials_json)
                    except Exception as exc:
                        logger.warning(
                            "Failed to decrypt credentials for profile %s: %s",
                            run.credential_profile_id,
                            exc,
                        )

        event_sink = _make_live_event_sink(run_id)

        db.close()
        db = None

        # Run execution engine (synchronous wrapper around async playwright/browser-use)
        from app.services.ai_runner import run_ai_test_sync

        result = run_ai_test_sync(
            goal=run.goal,
            environment_url=environment_url,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            on_event=event_sink,
        )

        # Re-open DB to persist results
        db = SessionLocal()
        run = db.get(AITestRun, run_id)
        if run is None:
            return

        # Check if cancelled while running
        if run.status == AIRunStatus.cancelled:
            logger.info("AI run %s was cancelled during execution", run_id)
            return

        # Reconcile events: these already streamed live via event_sink during
        # execution. This upsert pass just fills in anything that failed to
        # persist live (e.g. a transient DB hiccup) — it won't create
        # duplicate rows for events that already made it in.
        for event_data in result.get("events", []):
            _upsert_ai_run_event(db, run_id, event_data)

        completed_at = datetime.now(timezone.utc)
        started = (
            run.started_at.replace(tzinfo=timezone.utc)
            if run.started_at and run.started_at.tzinfo is None
            else run.started_at
        )
        duration_ms = (
            int((completed_at - started).total_seconds() * 1000) if started else None
        )

        failing = result.get("failing_step")
        run.status = AIRunStatus(result["status"])
        run.completed_at = completed_at
        run.duration_ms = duration_ms
        run.step_count = len(result.get("events", []))
        run.summary = result.get("summary", "")
        if failing:
            run.failing_step_index = failing.get("sequence")
            run.failing_step_description = failing.get("description")
            run.failing_step_screenshot_url = failing.get("screenshot_url")

        db.commit()
        logger.info("AI run %s completed with status: %s", run_id, result["status"])

    except Exception as exc:
        logger.exception("AI run %s raised an unhandled exception: %s", run_id, exc)
        if db:
            try:
                from app.models.ai_runs import AIRunStatus, AITestRun
                run = db.get(AITestRun, run_id)
                if run and run.status == AIRunStatus.running:
                    run.status = AIRunStatus.inconclusive
                    run.summary = f"Unhandled execution error: {exc}"
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                pass
    finally:
        if db:
            db.close()
