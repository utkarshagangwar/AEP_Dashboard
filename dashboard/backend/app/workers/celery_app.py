"""Celery application configuration for the Automation Execution Platform."""
import os

from celery import Celery
from celery.schedules import crontab

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
        "app.workers.tasks.sow_impact",
        "app.workers.tasks.pi_ingest",
        "app.workers.tasks.pi_crawl",
        "app.workers.tasks.pi_embed",
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
    "sweep-pi-captures": {
        # Retry net only — the normal path extracts inline right after a
        # run finishes (see workers/tasks/pi_ingest.py). A no-op whenever
        # PI_ENABLED is unset, same as every other Project Intelligence
        # code path.
        "task": "workers.tasks.pi_ingest.sweep_pending_captures",
        "schedule": 300.0,  # every 5 minutes
    },
    # Project Intelligence Phase 3 (spec §14.3, table 13: "Celery Beat, per
    # project, staggered, default nightly"). Both a no-op whenever
    # PI_CRAWL_ENABLED is off (workers/tasks/pi_crawl.py checks first, before
    # any DB query) — adding these two entries changes nothing for a
    # deployment that has not explicitly turned crawling on.
    "pi-schedule-nightly-crawls": {
        "task": "workers.tasks.pi_crawl.schedule_nightly_crawls",
        "schedule": crontab(hour=2, minute=0),  # 02:00 UTC nightly
    },
    "pi-crawl-artifact-cleanup": {
        # After the nightly crawl window, not PI_CRAWL_ENABLED-gated (see
        # the task's own docstring) -- artifacts from crawling that ran
        # before crawling was turned off must still age out on schedule.
        "task": "workers.tasks.pi_crawl.cleanup_expired_artifacts",
        "schedule": crontab(hour=3, minute=0),  # 03:00 UTC nightly
    },
    # Project Intelligence Phase 5 (Scale). Both a no-op whenever
    # PI_SEMANTIC_SEARCH_ENABLED is off (workers/tasks/pi_embed.py checks
    # first, before any DB query) -- adding these two entries changes
    # nothing for a deployment that has not explicitly turned semantic
    # search on. Scheduled after the crawl window so a project's freshly
    # crawl-derived behavior notes (once reviewed) get picked up the same
    # night, not a day later.
    "pi-backfill-embeddings": {
        "task": "workers.tasks.pi_embed.backfill_embeddings",
        "schedule": crontab(hour=4, minute=0),  # 04:00 UTC nightly
    },
    "pi-cleanup-stale-embeddings": {
        "task": "workers.tasks.pi_embed.cleanup_stale_embeddings",
        "schedule": crontab(hour=4, minute=30),  # 04:30 UTC nightly
    },
}
