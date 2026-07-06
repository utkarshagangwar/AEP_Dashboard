"""Celery task — download selected Figma frames as reference PNGs (Phase 4b).

The API endpoint creates one design_artifacts row per selected frame with
parse_status='pending' (here meaning "download queued") and a provisional
sha256 derived from (file_key, node_id). This task renders + downloads each
frame, replaces the provisional sha with the real content hash, and marks the
row done/error individually — one bad frame never blocks the others.
"""
import hashlib
import os

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def provisional_sha(file_key: str, node_id: str) -> str:
    """Deterministic placeholder sha for a frame before its PNG is downloaded.

    Also serves as the dedupe key: re-importing the same frame of the same
    file finds this row (or its post-download content sha) instead of
    creating a duplicate.
    """
    return hashlib.sha256(f"figma:{file_key}:{node_id}".encode()).hexdigest()


@celery_app.task(
    name="figma_import.import_figma_frames_task",
    bind=True,
    max_retries=0,
)
def import_figma_frames_task(self, file_key: str, artifact_map: dict) -> None:
    """artifact_map: {node_id: artifact_id} for rows created by the API."""
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, ParseStatus
    from app.services import figma_service
    from app.workers.tasks.visual_audit import data_dir

    session = SessionLocal()

    def _mark_error(artifact_id: str, message: str) -> None:
        artifact = (
            session.query(DesignArtifact)
            .filter(DesignArtifact.id == artifact_id)
            .one_or_none()
        )
        if artifact is not None:
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = message[:2000]
            session.commit()

    try:
        node_ids = list(artifact_map.keys())

        # One export request for all frames (Figma renders them in a batch)
        try:
            image_urls = figma_service.export_frames(file_key, node_ids)
        except figma_service.FigmaError as exc:
            logger.warning("Figma import: export failed for %s: %s", file_key, exc)
            for artifact_id in artifact_map.values():
                _mark_error(artifact_id, str(exc))
            return

        ref_dir = os.path.join(data_dir(), "references")
        os.makedirs(ref_dir, exist_ok=True)

        for node_id, artifact_id in artifact_map.items():
            artifact = (
                session.query(DesignArtifact)
                .filter(DesignArtifact.id == artifact_id)
                .one_or_none()
            )
            if artifact is None:
                logger.error("Figma import: artifact %s not found", artifact_id)
                continue

            url = image_urls.get(node_id)
            if not url:
                _mark_error(artifact_id, "Figma could not render this frame.")
                continue

            try:
                content = figma_service.download_png(url)
            except figma_service.FigmaError as exc:
                _mark_error(artifact_id, str(exc))
                continue

            sha = hashlib.sha256(content).hexdigest()
            storage_path = os.path.join(ref_dir, f"{sha}.png")
            try:
                with open(storage_path, "wb") as fh:
                    fh.write(content)
            except OSError as exc:
                _mark_error(artifact_id, f"Could not save frame to disk: {exc}")
                continue

            artifact.sha256 = sha  # replace provisional hash with content hash
            artifact.storage_path = storage_path
            artifact.parse_status = ParseStatus.done
            artifact.parse_error = None
            session.commit()
            logger.info(
                "Figma import: frame %s saved as artifact %s", node_id, artifact_id
            )
    except Exception:
        logger.exception("Figma import: unexpected failure for file %s", file_key)
        session.rollback()
        try:
            for artifact_id in artifact_map.values():
                artifact = (
                    session.query(DesignArtifact)
                    .filter(DesignArtifact.id == artifact_id)
                    .one_or_none()
                )
                from app.models.visual_qa import ParseStatus as PS

                if artifact is not None and artifact.parse_status in (
                    PS.pending,
                    PS.processing,
                ):
                    artifact.parse_status = PS.error
                    artifact.parse_error = "Unexpected worker failure — see worker logs."
            session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Figma import: could not mark artifacts as errored")
    finally:
        session.close()
