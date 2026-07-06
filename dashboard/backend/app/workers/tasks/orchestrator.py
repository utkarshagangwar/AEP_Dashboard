"""Celery task — execute an orchestrated run ("The Brain")."""
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="orchestrator.run_orchestrator_task",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_orchestrator_task(self, run_id: str) -> None:
    """Thin wrapper around orchestrator.execute_run() — the outer safety net.

    execute_run() handles its own DB sessions and catches errors during
    planning/execution/persistence, but if something still escapes it (e.g.
    a DB connectivity failure during the very first load), this outer
    try/except guarantees the run never gets stuck in "running" — mirrors
    run_visual_audit_task's shape in app/workers/tasks/visual_audit.py.
    """
    from app.services import orchestrator

    try:
        orchestrator.execute_run(run_id)
    except Exception:
        logger.exception("Orchestrator: run %s raised an unhandled exception", run_id)
        from app.core.database import SessionLocal
        from app.models.orchestrator import OrchestratorRun, OrchestratorRunStatus

        db = SessionLocal()
        try:
            run = db.get(OrchestratorRun, run_id)
            if run and run.status not in (
                OrchestratorRunStatus.passed,
                OrchestratorRunStatus.failed,
                OrchestratorRunStatus.error,
                OrchestratorRunStatus.cancelled,
            ):
                run.status = OrchestratorRunStatus.error
                run.summary = None
                run.error_message = "Unhandled execution error — see worker logs."
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            logger.exception("Orchestrator: could not mark run %s as errored", run_id)
        finally:
            db.close()
