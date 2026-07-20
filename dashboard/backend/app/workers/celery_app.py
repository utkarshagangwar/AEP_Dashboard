"""Celery application configuration for the Automation Execution Platform."""
import os

from celery import Celery

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "aep_worker",
    broker=broker_url,
    backend=result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=1800,
    task_time_limit=3600,
)

celery_app.conf.update(
    include=[
        "app.workers.tasks.execution",
        "app.workers.tasks.ai_execution",
        "app.workers.tasks.visual_audit",
        "app.workers.tasks.sow_ingest",
        "app.workers.tasks.figma_import",
        "app.workers.tasks.video_ingest",
        "app.workers.tasks.orchestrator",
        "app.workers.tasks.visual_qa_reconcile",
        "app.workers.tasks.sow_ledger",
        "app.workers.tasks.sow_reconcile",
        "app.workers.tasks.sow_generation",
    ],
)

# ── Periodic tasks (requires the worker to run with -B, see docker-compose.yml) ──
# Stale-run reconciliation used to run inline on every Reports/summary API
# request (see reports.py history) — moved here so it runs on a fixed
# schedule regardless of whether anyone has the Reports page open.
celery_app.conf.beat_schedule = {
    "reconcile-stale-runs": {
        "task": "workers.tasks.execution.reconcile_stale_runs",
        "schedule": 300.0,  # every 5 minutes
    },
    "reconcile-stale-visual-qa": {
        "task": "visual_qa_reconcile.reconcile_stale_visual_qa",
        "schedule": 300.0,  # every 5 minutes
    },
    "reconcile-stale-sow-sources": {
        "task": "sow_reconcile.reconcile_stale_sow_sources",
        "schedule": 300.0,  # every 5 minutes
    },
}
