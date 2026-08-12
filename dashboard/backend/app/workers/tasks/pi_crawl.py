"""Celery tasks — Project Intelligence Phase 3: Active Scheduled Crawler.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §14.3, §24, table 13,
table 16, table 17. The actual crawl/vision-extraction/cleanup logic lives
in app/services/pi_crawl.py; this module is orchestration only — three
Celery Beat-triggered entry points registered in workers/celery_app.py:

  schedule_nightly_crawls    enumerates every eligible (project,
                              environment) pair and enqueues one
                              crawl_project_environment per pair, staggered
                              (spec table 13: "Celery Beat, per project,
                              staggered, default nightly")
  crawl_project_environment  runs one crawl. Re-validates every gate
                              itself (defense in depth — never trusts that
                              the scheduler's snapshot is still current by
                              the time this fires)
  cleanup_expired_artifacts  the retention sweep spec §24 requires
                              ("required before Phase 3 ships, together
                              with its cleanup task")

max_retries=0 on every task here, matching workers/tasks/pi_ingest.py's
convention: these are unattended, best-effort, and already re-run on the
next scheduled tick — a Celery-level retry would only risk pile-up against
a target that is transiently down.
"""
from celery import shared_task

from app.core.logging import get_logger

logger = get_logger(__name__)


def _feature_and_project_eligible(project, environment_row) -> tuple[bool, str]:
    """The full crawl-eligibility gate for one (project, environment) pair,
    shared by the scheduler (which only enqueues eligible pairs) and the
    per-pair task (which re-checks in case anything changed between
    scheduling and execution). Returns (eligible, reason-if-not)."""
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.crawl_enabled():
        return False, "PI_CRAWL_ENABLED is off"
    if project is None or not project.is_active:
        return False, "project not found or inactive"
    if not project.pi_crawl_enabled:
        return False, "project has not opted in (pi_crawl_enabled=false)"
    if environment_row is None or not environment_row.base_url:
        return False, "environment has no base_url configured"
    if environment_row.is_production and not project.pi_crawl_production_approved:
        return False, "environment is production and has not been approved for crawling"
    return True, ""


@shared_task(name="workers.tasks.pi_crawl.crawl_project_environment", bind=True, max_retries=0)
def crawl_project_environment(self, project_id: str, environment_id: str) -> dict:
    """Crawl one (project, environment) pair. Safe to call directly (not
    just via the scheduler) — every gate is re-checked here regardless of
    how the caller decided to invoke it."""
    from app.core.database import SessionLocal
    from app.models.project import Project, ProjectEnvironment
    from app.services import pi_crawl

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        environment_row = db.get(ProjectEnvironment, environment_id)

        eligible, reason = _feature_and_project_eligible(project, environment_row)
        if not eligible:
            logger.info(
                "pi_crawl task: skipping project=%s environment=%s (%s)",
                project_id, environment_id, reason,
            )
            return {"skipped": reason}

        return pi_crawl.run_crawl(db, project=project, environment_row=environment_row)
    except Exception:
        db.rollback()
        logger.exception(
            "pi_crawl task: crawl_project_environment failed for project=%s environment=%s",
            project_id, environment_id,
        )
        return {"error": "crawl failed"}
    finally:
        db.close()


@shared_task(name="workers.tasks.pi_crawl.schedule_nightly_crawls", bind=True, max_retries=0)
def schedule_nightly_crawls(self) -> dict:
    """Beat-triggered (see workers/celery_app.py's beat_schedule). Enumerates
    every currently-eligible (project, environment) pair and enqueues one
    crawl_project_environment per pair, spaced by
    pi_ingest.crawl_stagger_s() so a night with several eligible projects
    does not launch every crawl (and every Chromium instance) at once.
    A no-op — 0 scheduled, no DB query beyond the flag check — whenever
    PI_CRAWL_ENABLED is off, same fail-open posture as every other PI
    Beat entry (see sweep_pending_captures in workers/tasks/pi_ingest.py).
    """
    from app.core.database import SessionLocal
    from app.models.project import Project, ProjectEnvironment
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.crawl_enabled():
        return {"scheduled": 0}

    db = SessionLocal()
    try:
        stagger_s = pi_ingest_svc.crawl_stagger_s()
        scheduled = 0
        skipped = 0

        projects = (
            db.query(Project)
            .filter(Project.is_active.is_(True), Project.pi_crawl_enabled.is_(True))
            .all()
        )
        for project in projects:
            envs = (
                db.query(ProjectEnvironment)
                .filter(ProjectEnvironment.project_id == project.id)
                .order_by(ProjectEnvironment.environment)
                .all()
            )
            for env in envs:
                eligible, reason = _feature_and_project_eligible(project, env)
                if not eligible:
                    skipped += 1
                    continue
                crawl_project_environment.apply_async(
                    args=[str(project.id), str(env.id)],
                    countdown=scheduled * stagger_s,
                )
                scheduled += 1

        logger.info(
            "pi_crawl task: schedule_nightly_crawls enqueued %d crawl(s), skipped %d "
            "ineligible environment(s), staggered %ds apart",
            scheduled, skipped, stagger_s,
        )
        return {"scheduled": scheduled, "skipped": skipped}
    except Exception:
        logger.exception("pi_crawl task: schedule_nightly_crawls failed")
        return {"scheduled": 0}
    finally:
        db.close()


@shared_task(name="workers.tasks.pi_crawl.cleanup_expired_artifacts", bind=True, max_retries=0)
def cleanup_expired_artifacts(self) -> dict:
    """Beat-triggered nightly retention sweep (spec §24). A no-op whenever
    PI_ENABLED is off, same convention as sweep_pending_captures — this
    still runs even if PI_CRAWL_ENABLED is currently off, so artifacts from
    a crawl that ran while crawling WAS enabled still age out on schedule
    after it is turned off again."""
    from app.core.database import SessionLocal
    from app.services import pi_crawl
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.pi_enabled():
        return {"checked": 0, "deleted": 0, "evidence_refs_cleared": 0}

    db = SessionLocal()
    try:
        return pi_crawl.cleanup_expired_artifacts(db)
    except Exception:
        logger.exception("pi_crawl task: cleanup_expired_artifacts failed")
        return {"checked": 0, "deleted": 0, "evidence_refs_cleared": 0}
    finally:
        db.close()
