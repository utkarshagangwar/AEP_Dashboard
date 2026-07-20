"""Celery tasks — extract SOW requirements-ledger facts from an attached
source (Phase 1).

Lifecycle mirrors app.workers.tasks.sow_ingest / video_ingest exactly:
SowDocumentSource.status pending -> processing -> done|error, every
exception raised *within a live worker process* caught and written back as
status='error' + error_message. That does NOT cover the worker process
itself dying mid-extraction (container restart, OOM-kill, deploy) — a
source can be left stuck 'processing' forever with no exception ever
raised to catch. app.workers.tasks.sow_reconcile runs periodically to
detect and recover exactly that case, same as visual_qa_reconcile does for
the SOW Checkpoints/Video Walkthrough pipeline.

Unlike that pipeline's Memory Bank short-circuit (never re-parse the same
artifact twice), ledger extraction is scoped per (document, artifact) via
SowDocumentSource — the same uploaded file can be attached to two
different SOW documents and is extracted independently for each, since the
ledger facts belong to the document, not the artifact. This is a
deliberate Phase 1 simplification (see sow_ledger.py's module docstring);
revisit only if duplicate extraction cost across documents turns out to
matter in practice.
"""
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _save_facts(session, document_id, artifact_id, facts: list[dict]) -> int:
    """Insert validated ledger facts for this (document, artifact) pair.
    Facts are append-only within a single extraction run — this function is
    only ever called once per source per task execution, so there's no
    existing-row cleanup needed here (a re-extraction, e.g. after a Retry,
    goes through the same source row and would otherwise duplicate facts;
    see the callers below, which delete this source's prior facts first)."""
    from app.models.sow import SowLedgerFactType, SowRequirementsLedger, SowUIElementType

    # sow_ledger.py's validator returns plain strings (raw JSON values from
    # the LLM, already checked against the valid value sets) -- converted to
    # actual enum members here, matching this codebase's established
    # convention of never assigning raw strings to Enum-typed columns (see
    # e.g. visual_audit.py's artifact_type=ArtifactType.video everywhere).
    rows = [
        SowRequirementsLedger(
            document_id=document_id,
            source_artifact_id=artifact_id,
            fact_type=SowLedgerFactType(f["fact_type"]),
            element_type=SowUIElementType(f["element_type"]) if f["element_type"] else None,
            label=f["label"],
            location=f["location"],
            behavior_notes=f["behavior_notes"],
            source_ref=f.get("source_ref"),
        )
        for f in facts
    ]
    session.add_all(rows)
    return len(rows)


def _clear_prior_facts(session, document_id, artifact_id) -> None:
    """Delete this source's previously-extracted facts before re-running --
    a Retry must replace stale facts, never append duplicates alongside
    them."""
    from app.models.sow import SowRequirementsLedger

    session.query(SowRequirementsLedger).filter(
        SowRequirementsLedger.document_id == document_id,
        SowRequirementsLedger.source_artifact_id == artifact_id,
    ).delete(synchronize_session=False)


def _mark_unexpected_failure(source_id: str) -> None:
    """Best-effort error-state recovery shared by all three tasks' outer
    except blocks — opens its own fresh session since the one in scope at
    the point of failure may itself be poisoned."""
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus

    session = SessionLocal()
    try:
        source = session.get(SowDocumentSource, source_id)
        if source is not None:
            source.status = SowSourceStatus.error
            source.error_message = "Unexpected worker failure — see worker logs."
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("SOW ledger: could not mark source %s as errored", source_id)
    finally:
        session.close()


@celery_app.task(name="sow_ledger.extract_transcript_ledger_task", bind=True, max_retries=0)
def extract_transcript_ledger_task(self, source_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus
    from app.services import sow_ledger
    from app.services.design_ingest import IngestError, extract_text

    session = SessionLocal()
    try:
        from app.models.visual_qa import DesignArtifact

        source = session.get(SowDocumentSource, source_id)
        if source is None or source.artifact_id is None:
            logger.error("SOW ledger: transcript source %s not found", source_id)
            return
        # Plain FK lookup, not an ORM relationship -- this codebase's SOW/
        # Visual QA models deliberately don't declare relationship() (see
        # app/models/visual_qa.py, app/models/sow.py), so every cross-table
        # read is an explicit query.
        artifact = session.get(DesignArtifact, source.artifact_id)
        if artifact is None:
            source.status = SowSourceStatus.error
            source.error_message = "Underlying file is missing (artifact was deleted)."
            session.commit()
            return

        source.status = SowSourceStatus.processing
        session.commit()

        try:
            text = extract_text(artifact.storage_path, artifact.file_name)
            facts, model_used = sow_ledger.extract_ledger_from_transcript(text)
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            session.commit()
            logger.warning("SOW ledger: transcript source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        source.status = SowSourceStatus.done
        source.error_message = None
        source.ledger_fact_count = count
        session.commit()
        logger.info(
            "SOW ledger: transcript source %s -> %d fact(s) via %s",
            source_id, count, model_used,
        )
    except Exception:
        logger.exception("SOW ledger: unexpected failure for transcript source %s", source_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(source_id)
        return
    finally:
        session.close()


@celery_app.task(
    name="sow_ledger.extract_recording_ledger_task",
    bind=True,
    max_retries=0,
    # Same rationale as video_ingest.ingest_video_task: the slowest job in
    # this pipeline (Gemini Files API upload + preprocessing poll +
    # generateContent over a full recording) — a soft limit below the
    # global 1800s default keeps a hung upload from occupying the worker.
    soft_time_limit=1200,
)
def extract_recording_ledger_task(self, source_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus
    from app.services import sow_ledger
    from app.services.design_ingest import IngestError

    session = SessionLocal()
    try:
        from app.models.visual_qa import DesignArtifact

        source = session.get(SowDocumentSource, source_id)
        if source is None or source.artifact_id is None:
            logger.error("SOW ledger: recording source %s not found", source_id)
            return
        artifact = session.get(DesignArtifact, source.artifact_id)
        if artifact is None:
            source.status = SowSourceStatus.error
            source.error_message = "Underlying file is missing (artifact was deleted)."
            session.commit()
            return

        source.status = SowSourceStatus.processing
        session.commit()

        try:
            facts, model_used = sow_ledger.extract_ledger_from_recording(
                artifact.storage_path,
                artifact.file_name,
                context_label=artifact.platform_name,
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            session.commit()
            logger.warning("SOW ledger: recording source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        source.status = SowSourceStatus.done
        source.error_message = None
        source.ledger_fact_count = count
        session.commit()
        logger.info(
            "SOW ledger: recording source %s -> %d fact(s) via %s",
            source_id, count, model_used,
        )
    except Exception:
        logger.exception("SOW ledger: unexpected failure for recording source %s", source_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(source_id)
        return
    finally:
        session.close()


@celery_app.task(name="sow_ledger.extract_design_ledger_task", bind=True, max_retries=0)
def extract_design_ledger_task(self, source_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus
    from app.services import sow_ledger
    from app.services.design_ingest import IngestError

    session = SessionLocal()
    try:
        from app.models.visual_qa import DesignArtifact

        source = session.get(SowDocumentSource, source_id)
        if source is None or source.artifact_id is None:
            logger.error("SOW ledger: design source %s not found", source_id)
            return
        artifact = session.get(DesignArtifact, source.artifact_id)
        if artifact is None:
            source.status = SowSourceStatus.error
            source.error_message = "Underlying file is missing (artifact was deleted)."
            session.commit()
            return

        source.status = SowSourceStatus.processing
        session.commit()

        try:
            with open(artifact.storage_path, "rb") as fh:
                image_bytes = fh.read()
        except OSError as exc:
            source.status = SowSourceStatus.error
            source.error_message = f"Could not read design file: {exc}"
            session.commit()
            logger.warning("SOW ledger: design source %s file read failed: %s", source_id, exc)
            return

        try:
            facts, model_used = sow_ledger.extract_ledger_from_image(
                image_bytes, artifact.file_name, context_label=artifact.target_page
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            session.commit()
            logger.warning("SOW ledger: design source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        source.status = SowSourceStatus.done
        source.error_message = None
        source.ledger_fact_count = count
        session.commit()
        logger.info(
            "SOW ledger: design source %s -> %d fact(s) via %s",
            source_id, count, model_used,
        )
    except Exception:
        logger.exception("SOW ledger: unexpected failure for design source %s", source_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(source_id)
        return
    finally:
        session.close()
