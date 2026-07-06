"""Celery task — ingest a SOW artifact into checkpoints (Phase 3, The Brain).

Memory Bank contract: if a design_rules row already exists for the artifact,
the task exits immediately — a document is never parsed (and never costs
tokens) twice. Every failure path writes parse_status='error' + parse_error
so the UI never shows a document stuck in 'processing'.
"""
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="sow_ingest.ingest_sow_task",
    bind=True,
    max_retries=0,
)
def ingest_sow_task(self, artifact_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, DesignRule, ParseStatus
    from app.services import design_ingest

    session = SessionLocal()
    try:
        artifact = (
            session.query(DesignArtifact)
            .filter(DesignArtifact.id == artifact_id)
            .one_or_none()
        )
        if artifact is None:
            logger.error("SOW ingest: artifact %s not found", artifact_id)
            return

        # Memory Bank hit — already parsed, do not spend tokens again.
        existing = (
            session.query(DesignRule)
            .filter(DesignRule.artifact_id == artifact.id)
            .first()
        )
        if existing:
            artifact.parse_status = ParseStatus.done
            artifact.parse_error = None
            session.commit()
            logger.info("SOW ingest: artifact %s already parsed, skipping", artifact_id)
            return

        artifact.parse_status = ParseStatus.processing
        session.commit()

        try:
            text = design_ingest.extract_text(artifact.storage_path, artifact.file_name)
            checkpoints, model_used = design_ingest.parse_sow(text)
        except design_ingest.IngestError as exc:
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = str(exc)
            session.commit()
            logger.warning("SOW ingest: artifact %s failed: %s", artifact_id, exc)
            return

        session.add(
            DesignRule(
                artifact_id=artifact.id,
                checkpoints=checkpoints,
                parsed_by_model=model_used,
            )
        )
        artifact.parse_status = ParseStatus.done
        artifact.parse_error = None
        session.commit()
        logger.info(
            "SOW ingest: artifact %s parsed into %d checkpoint(s)",
            artifact_id,
            len(checkpoints),
        )
    except Exception:
        logger.exception("SOW ingest: unexpected failure for %s", artifact_id)
        session.rollback()
        try:
            artifact = (
                session.query(DesignArtifact)
                .filter(DesignArtifact.id == artifact_id)
                .one_or_none()
            )
            if artifact is not None:
                from app.models.visual_qa import ParseStatus as PS

                artifact.parse_status = PS.error
                artifact.parse_error = "Unexpected worker failure — see worker logs."
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("SOW ingest: could not mark artifact %s as errored", artifact_id)
    finally:
        session.close()
