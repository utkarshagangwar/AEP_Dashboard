"""Celery task — recover sow_document_sources rows stuck in 'processing'.

Direct analogue of app.workers.tasks.visual_qa_reconcile for the SOW
ledger-extraction pipeline (Phase 1). A SowDocumentSource only ever leaves
'processing' via the success or exception path inside the three tasks in
app.workers.tasks.sow_ledger — all running inside a live worker process. If
that process dies mid-extraction (container restart, OOM-kill, deploy)
instead of raising a catchable exception, the row is stuck 'processing'
forever: nothing else ever resets it, and the frontend's Retry affordance
only makes sense for 'error' (see plan §11.2 for the general rule this
implements). There is nothing to recover here — the LLM call simply never
completed — so this just marks the row 'error' and hands it back to the
Retry path.
"""
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Text extraction is a single completion call (well under a minute
# normally); a recording digest goes through the same Gemini Files API
# upload/poll/generate flow as video_ingest.ingest_video_task and shares
# its soft_time_limit=1200s (20min) backstop -- sit above that so a still-
# healthy, still-running task gets the chance to hit its own limit and
# self-report as an error before this sweep would (same reasoning as
# visual_qa_reconcile's _VIDEO_STALE_MINUTES).
_TEXT_OR_IMAGE_STALE_MINUTES = 10
_RECORDING_STALE_MINUTES = 25


def _is_stale(updated_at, cutoff_minutes: int, now: datetime) -> bool:
    aware = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at
    return (now - aware) > timedelta(minutes=cutoff_minutes)


@celery_app.task(name="sow_reconcile.reconcile_stale_sow_sources", bind=True, max_retries=0)
def reconcile_stale_sow_sources(self) -> dict:
    """Periodic task (scheduled in celery_app.py's beat_schedule, every 5 min)."""
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus
    from app.models.visual_qa import ArtifactType, DesignArtifact

    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        candidates = (
            session.query(SowDocumentSource)
            .filter(SowDocumentSource.status == SowSourceStatus.processing)
            .all()
        )

        # Recordings get the longer threshold (Gemini Files API upload +
        # preprocessing poll + generateContent); transcripts and design
        # images get the shorter one. Look up each candidate's
        # artifact_type to pick the right threshold -- one extra query per
        # candidate, acceptable since stuck rows are the rare case, not
        # the common one.
        stale = []
        for source in candidates:
            artifact = (
                session.query(DesignArtifact.artifact_type)
                .filter(DesignArtifact.id == source.artifact_id)
                .first()
            )
            is_recording = artifact is not None and artifact[0] == ArtifactType.meeting_recording
            threshold = _RECORDING_STALE_MINUTES if is_recording else _TEXT_OR_IMAGE_STALE_MINUTES
            if _is_stale(source.updated_at, threshold, now):
                stale.append(source)

        for source in stale:
            logger.warning(
                "sow reconcile: source %s (document %s) stuck processing since %s, marking error",
                source.id, source.document_id, source.updated_at,
            )
            source.status = SowSourceStatus.error
            source.error_message = (
                "Extraction was interrupted (the worker restarted mid-run) — click Retry."
            )

        session.commit()
        if stale:
            logger.warning("sow reconcile: reset %d stale source(s)", len(stale))
        return {"reconciled_sources": len(stale)}
    except Exception as exc:
        logger.exception("sow reconcile: reconcile_stale_sow_sources failed")
        session.rollback()
        return {"error": str(exc)}
    finally:
        session.close()
