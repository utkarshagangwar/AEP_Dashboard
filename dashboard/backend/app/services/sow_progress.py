"""Live extraction progress for SOW ingest.

WHAT THIS IS FOR. The Skills & TDDs panel shows what the pipeline is doing
right now. The tempting implementation — a fixed list of phases in the UI,
ticked off by inspecting SowPart rows — cannot be honest: it shows the same
steps in the same order regardless of what ran, claiming "identifying feature
sections" on an ingest with TDD_ZONING=0, and staying silent on gap repair,
the variant cap and the cross-part merge, which are precisely the stages a
reader needs to know fired. PRODUCT.md's first design principle is that copy
must never claim progress that isn't happening. So the events are written by
the code that does the work, and the panel renders whatever it finds.

WHY ITS OWN SESSION — the load-bearing detail. The worker holds one
transaction open for an entire part: extraction, repair, dedupe, skill
creation. An event written on that session is invisible to every reader until
the part commits, i.e. until the work it describes has already finished.
Progress that only appears once there is no longer any progress to report is
worse than none. Each emit therefore opens its own short-lived session,
commits, and closes.

The consequence is deliberate: events survive a rolled-back part. "Extraction
started and then failed" is the state a reader most needs, and a panel that
blanks itself on failure hides the one run worth looking at.

NEVER RAISES. Progress reporting cannot be allowed to fail an ingest. Every
path swallows and logs — a missing row costs a line in a panel, an exception
here would cost the extraction.
"""
from __future__ import annotations

from typing import Callable, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Status vocabulary. `skipped` exists separately from `done` because "the
# repair pass found nothing to repair" and "the repair pass never ran" are
# different facts, and reporting a disabled flag as completed work is exactly
# the dishonesty this module was built to avoid.
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
ERROR = "error"


def emit(
    artifact_id,
    stage: str,
    status: str,
    description: str,
    *,
    part_number: int | None = None,
    detail: dict | None = None,
) -> None:
    """Append one progress event. Best-effort; never raises."""
    from app.core.database import SessionLocal
    from app.models.visual_qa import SowIngestEvent

    session = None
    try:
        session = SessionLocal()
        # Per-artifact sequence. Safe without locking because SOW ingest is
        # single-flight per document — never two parts of one artifact in
        # flight (see sow_ingest._chain_next_part).
        last = (
            session.query(SowIngestEvent.sequence)
            .filter(SowIngestEvent.artifact_id == artifact_id)
            .order_by(SowIngestEvent.sequence.desc())
            .first()
        )
        session.add(
            SowIngestEvent(
                artifact_id=artifact_id,
                part_number=part_number,
                sequence=(last[0] + 1) if last else 1,
                stage=stage[:40],
                status=status[:20],
                description=description[:2000],
                detail=detail or None,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 — a panel row is never worth an ingest
        logger.warning(
            "SOW progress: could not record %r for artifact %s",
            stage, artifact_id, exc_info=True,
        )
        if session is not None:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


# The signature pure code receives: (stage, status, description, detail).
ProgressFn = Callable[[str, str, str, Optional[dict]], None]


def reporter(artifact_id, part_number: int | None = None) -> ProgressFn:
    """A bound emit() to hand to code that must not know about the database.

    app.services.tdd_extraction is deliberately DB-free — its whole test suite
    runs with no database — so it takes a callback rather than a session. This
    is what binds that callback to an artifact.
    """

    def _report(stage: str, status: str, description: str, detail: dict | None = None) -> None:
        emit(
            artifact_id,
            stage,
            status,
            description,
            part_number=part_number,
            detail=detail,
        )

    return _report


def report(on_progress: ProgressFn | None, stage: str, status: str, description: str,
           detail: dict | None = None) -> None:
    """Call a progress callback if one was supplied.

    Every emitting site in the extraction engine is optional — that engine is
    also used by callers with no artifact at all — so this keeps the call
    sites free of `if on_progress is not None` noise, and makes a badly
    behaved callback (one that raises) unable to break extraction either.
    """
    if on_progress is None:
        return
    try:
        on_progress(stage, status, description, detail)
    except Exception:  # noqa: BLE001
        logger.warning("SOW progress: callback failed for %r", stage, exc_info=True)


def clear(artifact_id) -> None:
    """Drop an artifact's events before a fresh ingest of the same artifact.

    Called only on a deliberate re-analysis (the Retry path). Without it, a
    retried document shows the failed attempt's steps above the new ones with
    nothing marking where one ended and the other began, which reads as a
    single very confused run.
    """
    from app.core.database import SessionLocal
    from app.models.visual_qa import SowIngestEvent

    session = None
    try:
        session = SessionLocal()
        session.query(SowIngestEvent).filter(
            SowIngestEvent.artifact_id == artifact_id
        ).delete(synchronize_session=False)
        session.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "SOW progress: could not clear events for artifact %s",
            artifact_id, exc_info=True,
        )
        if session is not None:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
