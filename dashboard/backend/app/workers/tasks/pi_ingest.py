"""Celery tasks: turn a finished test run's raw capture into Project
Intelligence rows.

Two entry points, one per source, both fire-and-forget (`.delay(...)`,
never awaited) from the task that just finished a run:
  ingest_rf_capture   — called from workers/tasks/execution.py after a
                         robot/pabot suite exits, reads the JSON sidecar
                         workers/tasks/rf_listener.py wrote during the run.
  ingest_vibe_capture — called from workers/tasks/ai_execution.py after an
                         AI Vibe Test run persists its result, reads the
                         run's own history_json (no sidecar needed — the
                         Vibe path already keeps this in the DB).

Plus one periodic sweep (registered in celery_app.py's beat_schedule) that
retries any pi_capture_events row extraction failed on the first pass.

Every task here starts with the pi_enabled()/*_capture_enabled() gate and
is wrapped end-to-end in try/except: a Project Intelligence failure must
never surface anywhere the test run itself is observed from, and because
every call site here is a `.delay()` nobody waits on, an uncaught
exception would only ever show up in a Celery worker log — still worth
catching explicitly so it doesn't get mistaken for the run's own tasks
misbehaving.
"""
import json
import os

from celery import shared_task

from app.core.logging import get_logger

logger = get_logger(__name__)


def _sidecar_path(results_dir: str) -> str:
    return os.path.join(results_dir, "pi_capture.json")


def _maybe_propose_flow(db, *, project_id, run_id, stats: dict) -> None:
    """Only propose a new flow version when the screen/edge graph actually
    changed — otherwise every single run (most of which re-observe an
    unchanged product) would push a new pending version into the review
    queue for no reason. Best-effort: a failed proposal here does not
    undo the extraction that already committed above it."""
    if not (stats.get("screens_new") or stats.get("edges_new")):
        return
    try:
        from app.services import pi_flow

        pi_flow.propose_model(
            db, project_id=project_id, environment_id=None,
            generated_from_run_ids=[str(run_id)],
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "pi_ingest task: flow proposal failed for project %s (run %s)",
            project_id, run_id, exc_info=True,
        )


@shared_task(name="workers.tasks.pi_ingest.ingest_rf_capture", bind=True, max_retries=0)
def ingest_rf_capture(self, run_id: str, results_dir: str) -> dict:
    """Read the pi_capture.json sidecar (if any) rf_listener.py wrote for
    this run, resolve the owning project via test_runs -> test_suites, and
    extract it into pi_screens/pi_components/pi_navigation_edges/
    pi_behavior_notes. A missing sidecar (feature was off during the run,
    or the run produced no captured actions) is a normal no-op, not an
    error — RF suites predate this feature and must keep working exactly
    as before whether or not a sidecar was ever written.
    """
    from app.core.database import SessionLocal
    from app.services import pi_extract
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.pi_enabled() or not pi_ingest_svc.rf_capture_enabled():
        return {"skipped": "disabled"}

    sidecar = _sidecar_path(results_dir)
    if not os.path.isfile(sidecar):
        return {"skipped": "no sidecar"}

    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        logger.warning("pi_ingest task: could not read sidecar %s", sidecar, exc_info=True)
        return {"skipped": "unreadable sidecar"}

    raw_events = payload.get("raw_events") if isinstance(payload, dict) else None
    if not raw_events:
        return {"skipped": "no events"}

    db = SessionLocal()
    try:
        from app.models.test_run import TestRun
        from app.models.test_suite import TestSuite

        run = db.get(TestRun, run_id)
        if run is None:
            return {"skipped": "run not found"}
        suite = db.get(TestSuite, run.test_suite_id)
        if suite is None:
            return {"skipped": "suite not found"}
        project_id = suite.project_id

        screens = pi_ingest_svc.normalize_rf_events(raw_events)
        if not screens:
            return {"skipped": "nothing normalized"}

        event = pi_ingest_svc.write_capture_event(
            db, project_id=project_id, source_type="rf", source_run_id=run_id,
            payload_json={"environment_id": None, "screens": screens},
        )
        if event is None:
            db.rollback()
            return {"skipped": "capture event write failed"}

        stats = pi_extract.process_capture_event(db, event)
        db.commit()

        _maybe_propose_flow(db, project_id=project_id, run_id=run_id, stats=stats)
        return stats
    except Exception:
        db.rollback()
        logger.exception("pi_ingest task: ingest_rf_capture failed for run %s", run_id)
        return {"error": "ingest failed"}
    finally:
        db.close()


@shared_task(name="workers.tasks.pi_ingest.ingest_vibe_capture", bind=True, max_retries=0)
def ingest_vibe_capture(self, run_id: str, project_id, history_json) -> dict:
    """Extract an AI Vibe Test run's action history into the same
    pi_screens/pi_components/... tables the RF path writes to. `project_id`
    is passed in by the caller (workers/tasks/ai_execution.py already has
    it on the run row) rather than looked up here, since AITestRun's own
    session may already be closed by the time this fires.
    """
    from app.core.database import SessionLocal
    from app.services import pi_extract
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.pi_enabled() or not pi_ingest_svc.vibe_capture_enabled():
        return {"skipped": "disabled"}
    if not project_id:
        return {"skipped": "no project_id"}

    db = SessionLocal()
    try:
        screens = pi_ingest_svc.normalize_vibe_history(history_json)
        if not screens:
            return {"skipped": "nothing normalized"}

        event = pi_ingest_svc.write_capture_event(
            db, project_id=project_id, source_type="vibe", source_run_id=run_id,
            payload_json={"environment_id": None, "screens": screens},
        )
        if event is None:
            db.rollback()
            return {"skipped": "capture event write failed"}

        stats = pi_extract.process_capture_event(db, event)
        db.commit()

        _maybe_propose_flow(db, project_id=project_id, run_id=run_id, stats=stats)
        return stats
    except Exception:
        db.rollback()
        logger.exception("pi_ingest task: ingest_vibe_capture failed for run %s", run_id)
        return {"error": "ingest failed"}
    finally:
        db.close()


@shared_task(name="workers.tasks.pi_ingest.sweep_pending_captures")
def sweep_pending_captures() -> dict:
    """Periodic retry net (celery_app.py's beat_schedule, every 5 min) for
    pi_capture_events rows whose synchronous extraction above failed
    transiently (a DB hiccup, a momentary lock). The normal path already
    extracts inline inside ingest_rf_capture/ingest_vibe_capture — this is
    only a safety net, matching reconcile_stale_runs' role for test runs.
    """
    from app.core.database import SessionLocal
    from app.services import pi_extract
    from app.services import pi_ingest as pi_ingest_svc

    if not pi_ingest_svc.pi_enabled():
        return {"processed": 0}

    db = SessionLocal()
    try:
        return pi_extract.process_pending(db, limit=100)
    except Exception:
        logger.exception("pi_ingest task: sweep_pending_captures failed")
        return {"processed": 0}
    finally:
        db.close()
