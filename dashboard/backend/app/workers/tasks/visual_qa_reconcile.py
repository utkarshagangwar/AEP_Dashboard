"""Celery task — recover SOW/video ingestion rows stuck in 'processing'.

A SowPart/DesignArtifact only ever leaves 'processing' via the success or
exception path inside sow_ingest._analyze_part / video_ingest.ingest_video_task
— both running inside a live worker process. If that process dies mid-flight
(container restart, OOM-kill, deploy) instead of raising a catchable
exception, the row is stuck 'processing' forever: the API rejects
re-triggering analysis on anything but 'pending'/'error', and the frontend
only renders a Retry button for those same two states — so without this
task, a stuck row has no way back.

Mirrors workers.tasks.execution.reconcile_stale_runs: runs on a Celery beat
schedule (see celery_app.py), detects staleness via updated_at (no dedicated
heartbeat column — SowPart.updated_at / DesignArtifact.updated_at already
bump on every processing-state write via onupdate=func.now()). Unlike
reconcile_stale_runs, which can recover Robot Framework's on-disk output.xml,
there's nothing to recover here — the LLM call simply never completed — so
this just marks the row 'error' and hands it back to the existing Retry
button.
"""
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# LLM checkpoint extraction for one SOW part is a single completion call and
# normally finishes in well under a minute even on a slow provider — 10min
# mirrors the existing STALE_RUN_THRESHOLD_MINUTES convention
# (app.api.v1.executions) used for the analogous test-run reconciler.
_SOW_PART_STALE_MINUTES = 10
# Video digestion is genuinely slow (Gemini Files API upload + preprocessing
# poll + generateContent over the whole video) and already has its own
# soft_time_limit=1200s (20min) backstop in ingest_video_task — sit above
# that so a still-healthy, still-running task gets the chance to hit its own
# limit and self-report as an error before this sweep would.
_VIDEO_STALE_MINUTES = 25


def _is_stale(updated_at, cutoff_minutes: int, now: datetime) -> bool:
    aware = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at
    return (now - aware) > timedelta(minutes=cutoff_minutes)


@celery_app.task(name="visual_qa_reconcile.reconcile_stale_visual_qa", bind=True, max_retries=0)
def reconcile_stale_visual_qa(self) -> dict:
    """Periodic task (scheduled in celery_app.py's beat_schedule, every 5 min)."""
    from app.core.database import SessionLocal
    from app.models.visual_qa import ArtifactType, DesignArtifact, ParseStatus, SowPart
    from app.workers.tasks.sow_ingest import _recompute_artifact_status

    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # 1. Parts stuck 'processing' — the common case, since every SOW
        # document (single- or multi-part) always has at least one SowPart.
        candidate_parts = (
            session.query(SowPart).filter(SowPart.status == ParseStatus.processing).all()
        )
        stale_parts = [p for p in candidate_parts if _is_stale(p.updated_at, _SOW_PART_STALE_MINUTES, now)]
        affected_artifact_ids = set()
        for part in stale_parts:
            logger.warning(
                "visual_qa reconcile: part %s of artifact %s stuck processing since %s, marking error",
                part.part_number, part.artifact_id, part.updated_at,
            )
            part.status = ParseStatus.error
            part.error = "Analysis was interrupted (the worker restarted mid-run) — click Retry."
            affected_artifact_ids.add(part.artifact_id)
        if stale_parts:
            session.flush()
            for artifact_id in affected_artifact_ids:
                artifact = session.get(DesignArtifact, artifact_id)
                if artifact is not None:
                    _recompute_artifact_status(session, artifact)

        # 2. Backstop: an artifact stuck 'processing' with no SowPart rows at
        # all — every video artifact (never creates parts) plus the narrow
        # window where a SOW worker died before chunking ever ran. Rows
        # already handled by step 1 are excluded implicitly: recompute above
        # always leaves parse_status as 'done' or 'pending', never
        # 'processing', so they won't match this query.
        candidates = (
            session.query(DesignArtifact)
            .filter(
                DesignArtifact.parse_status == ParseStatus.processing,
                DesignArtifact.artifact_type.in_([ArtifactType.sow, ArtifactType.video]),
            )
            .all()
        )
        stale_artifact_count = 0
        for artifact in candidates:
            threshold = (
                _VIDEO_STALE_MINUTES if artifact.artifact_type == ArtifactType.video
                else _SOW_PART_STALE_MINUTES
            )
            if not _is_stale(artifact.updated_at, threshold, now):
                continue
            has_parts = (
                session.query(SowPart.id).filter(SowPart.artifact_id == artifact.id).first()
                is not None
            )
            if has_parts:
                continue
            logger.warning(
                "visual_qa reconcile: artifact %s (%s) stuck processing since %s with no parts, marking error",
                artifact.id, artifact.artifact_type, artifact.updated_at,
            )
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = (
                "Ingestion was interrupted (the worker restarted mid-run) — re-upload or retry."
            )
            stale_artifact_count += 1

        session.commit()
        if stale_parts or stale_artifact_count:
            logger.warning(
                "visual_qa reconcile: reset %d stale part(s) across %d document(s), "
                "%d stale artifact(s) with no parts",
                len(stale_parts), len(affected_artifact_ids), stale_artifact_count,
            )
        return {
            "reconciled_parts": len(stale_parts),
            "reconciled_artifacts": stale_artifact_count,
        }
    except Exception as exc:
        logger.exception("visual_qa reconcile: reconcile_stale_visual_qa failed")
        session.rollback()
        return {"error": str(exc)}
    finally:
        session.close()
