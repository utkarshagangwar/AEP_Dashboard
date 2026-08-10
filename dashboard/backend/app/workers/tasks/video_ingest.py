"""Celery task — digest a walkthrough video into checkpoints (Phase 5).

Identical lifecycle to sow_ingest: Memory Bank short-circuit (a video is
never digested — or billed — twice), terminal error states on every failure
path, nothing ever left stuck in 'processing'.
"""
from app.core.logging import get_logger
from app.services import ai_usage
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _save_functional_skills(session, artifact, checkpoints: list[dict]) -> None:
    """Save every functional checkpoint from this digest directly as a
    skill — a detailed prompt instruction, no live browser run required.
    Visual checkpoints are skipped.

    Each checkpoint is saved in its own SAVEPOINT (session.begin_nested()),
    with an explicit flush to force any DB error (e.g. two checkpoints
    slugifying to the same source_key) to surface right there instead of
    silently poisoning the whole transaction at the final commit. A single
    bad checkpoint is logged and skipped; digestion is never failed by a
    skill-capture problem.

    Checkpoints with grounding="derived" — the negative/edge cases
    classify_and_expand reasoned from the observed happy path rather than
    seeing on screen — become skills by default, and are held back when
    TDD_DERIVED_AS_SKILLS is disabled. Same rule as the SOW worker, because
    the flag describes the Skills TABLE, not one source of it: a flag that
    silently applied to documents but not to videos would be worse than no
    flag, since the table would look filtered while half of it was not."""
    from app.services.skill_store import upsert_prompt_skill
    from app.services.tdd_extraction import derived_as_skills

    allow_derived = derived_as_skills()
    seen_titles: set[str] = set()
    for i, cp in enumerate(checkpoints):
        if cp.get("type") != "functional" or not cp.get("description"):
            continue
        if cp.get("grounding") == "derived" and not allow_derived:
            logger.info(
                "Video ingest: held derived checkpoint %r from skill creation "
                "(TDD_DERIVED_AS_SKILLS is disabled)",
                cp.get("title") or "Untitled requirement",
            )
            continue
        title = (cp.get("title") or cp["description"][:80]).strip()
        # Test type is part of the identity: the observed positive case and
        # the derived negative case for the same behaviour may carry the same
        # title, and must not collide on source_key.
        dedup_key = f"{title.lower()}|{cp.get('test_type') or ''}"
        if dedup_key in seen_titles:
            title = f"{title} ({i + 1})"
        seen_titles.add(dedup_key)

        try:
            with session.begin_nested():
                upsert_prompt_skill(
                    session,
                    title=title,
                    instruction=cp["description"],
                    source_type="video",
                    artifact_id=artifact.id,
                    project_id=artifact.project_id,
                    test_type=cp.get("test_type"),
                    category=cp.get("category"),
                    grounding=cp.get("grounding"),
                    behaviour_key=cp.get("behaviour_key"),
                    priority=cp.get("priority"),
                )
                session.flush()
        except Exception:
            logger.exception(
                "Video ingest: failed to save skill for checkpoint %r of artifact %s "
                "— skipped, other checkpoints processed normally",
                title, artifact.id,
            )


@celery_app.task(
    name="video_ingest.ingest_video_task",
    bind=True,
    max_retries=0,
    # Video digestion is the slowest, highest-token job on the platform;
    # a lower soft limit than the global 1800s keeps a hung upload from
    # occupying the worker for half an hour.
    soft_time_limit=1200,
)
@ai_usage.tracked_task("video_import")
def ingest_video_task(self, artifact_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, DesignRule, ParseStatus
    from app.services import video_ingest
    from app.services.design_ingest import IngestError

    session = SessionLocal()
    try:
        artifact = (
            session.query(DesignArtifact)
            .filter(DesignArtifact.id == artifact_id)
            .one_or_none()
        )
        if artifact is None:
            logger.error("Video ingest: artifact %s not found", artifact_id)
            return

        # Memory Bank hit — already digested, do not spend tokens again.
        existing = (
            session.query(DesignRule)
            .filter(DesignRule.artifact_id == artifact.id)
            .first()
        )
        if existing:
            artifact.parse_status = ParseStatus.done
            artifact.parse_error = None
            session.commit()
            logger.info("Video ingest: artifact %s already digested, skipping", artifact_id)
            return

        artifact.parse_status = ParseStatus.processing
        session.commit()

        try:
            checkpoints, model_used = video_ingest.digest_video(
                artifact.storage_path,
                artifact.file_name,
                artifact.platform_name or "the uploaded application",
            )
        except IngestError as exc:
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = str(exc)
            session.commit()
            logger.warning("Video ingest: artifact %s failed: %s", artifact_id, exc)
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
        _save_functional_skills(session, artifact, checkpoints)
        session.commit()
        logger.info(
            "Video ingest: artifact %s digested into %d checkpoint(s)",
            artifact_id,
            len(checkpoints),
        )
    except Exception:
        logger.exception("Video ingest: unexpected failure for %s", artifact_id)
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
            logger.exception("Video ingest: could not mark artifact %s as errored", artifact_id)
    finally:
        session.close()
