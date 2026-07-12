"""Celery tasks — ingest a SOW artifact into checkpoints (Phase 3, The Brain).

Memory Bank contract: if a design_rules row already exists for the artifact,
ingest_sow_task exits immediately — a document is never parsed (and never
costs tokens) twice. Every exception raised *within a live worker process*
is caught and written back as parse_status='error' + parse_error. That does
NOT cover the worker process itself dying mid-analysis (container restart,
OOM-kill, deploy) — a part can be left stuck 'processing' forever with no
exception ever raised to catch. app.workers.tasks.visual_qa_reconcile runs
periodically to detect and recover exactly that case.

Number of parts is purely a function of document length divided by
design_ingest._CHUNK_MAX_CHARS (paragraph-aligned) — there is no cap on how
many parts a document can be split into.

Large documents (chunk_text) are split into sow_parts and analyzed one part
at a time — automatically for a single-part document (identical to the old
one-shot behavior), or one part per analyze_sow_part_task call, triggered
by the user, for a multi-part document. Checkpoints from every 'done' part
are merged (concatenated by part_number) into the artifact's single
design_rules row after each part completes.
"""
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _recompute_artifact_status(session, artifact) -> None:
    """Set artifact.parse_status from its parts: done iff every part is done,
    else pending (nothing currently running; waiting on the next part)."""
    from app.models.visual_qa import ParseStatus, SowPart

    statuses = [
        s
        for (s,) in session.query(SowPart.status)
        .filter(SowPart.artifact_id == artifact.id)
        .all()
    ]
    if statuses and all(s == ParseStatus.done for s in statuses):
        artifact.parse_status = ParseStatus.done
    else:
        artifact.parse_status = ParseStatus.pending


def _merge_checkpoints(session, artifact) -> None:
    """Recompute the artifact's DesignRule as the concatenation (by
    part_number) of every 'done' part's checkpoints. No cross-chunk dedup —
    simple, predictable concatenation."""
    from app.models.visual_qa import DesignRule, ParseStatus, SowPart

    done_parts = (
        session.query(SowPart)
        .filter(SowPart.artifact_id == artifact.id, SowPart.status == ParseStatus.done)
        .order_by(SowPart.part_number)
        .all()
    )
    checkpoints: list = []
    models_used: list[str] = []
    for p in done_parts:
        checkpoints.extend(p.checkpoints or [])
        if p.parsed_by_model and p.parsed_by_model not in models_used:
            models_used.append(p.parsed_by_model)

    rule = session.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
    if rule is None:
        rule = DesignRule(artifact_id=artifact.id, checkpoints=checkpoints)
        session.add(rule)
    rule.checkpoints = checkpoints
    rule.parsed_by_model = ", ".join(models_used) if models_used else None


def _analyze_part(session, artifact, part) -> None:
    """Analyze a single SowPart with the LLM, merge its checkpoints into the
    artifact's DesignRule, and recompute the artifact's overall status.
    Shared by ingest_sow_task (auto single-part case) and
    analyze_sow_part_task (manual multi-part case)."""
    from app.models.visual_qa import ParseStatus
    from app.services import design_ingest

    part.status = ParseStatus.processing
    artifact.parse_status = ParseStatus.processing
    session.commit()

    part_label = (
        f"part {part.part_number} of {artifact.total_parts}"
        if artifact.total_parts > 1
        else None
    )
    try:
        checkpoints, model_used = design_ingest.parse_sow(part.content, part_label=part_label)
    except design_ingest.IngestError as exc:
        part.status = ParseStatus.error
        part.error = str(exc)
        # autoflush is off for this session — flush explicitly so
        # _recompute_artifact_status' query below sees this part's new
        # status instead of the stale pre-update row.
        session.flush()
        _recompute_artifact_status(session, artifact)
        session.commit()
        logger.warning(
            "SOW ingest: artifact %s part %d failed: %s", artifact.id, part.part_number, exc
        )
        return

    part.checkpoints = checkpoints
    part.parsed_by_model = model_used
    part.status = ParseStatus.done
    # Same reason as above: flush before the helpers re-query SowPart rows.
    session.flush()
    _merge_checkpoints(session, artifact)
    _recompute_artifact_status(session, artifact)
    _save_functional_skills(session, artifact, checkpoints)
    session.commit()
    logger.info(
        "SOW ingest: artifact %s part %d/%d parsed into %d checkpoint(s) via %s",
        artifact.id,
        part.part_number,
        artifact.total_parts,
        len(checkpoints),
        model_used,
    )


def _save_functional_skills(session, artifact, checkpoints: list[dict]) -> None:
    """Save every functional checkpoint from this part directly as a skill —
    a detailed prompt instruction, no live browser run required. Visual
    checkpoints (pixel-diff/appearance claims) have nothing to execute, so
    they're skipped.

    Each checkpoint is saved in its own SAVEPOINT (session.begin_nested()),
    with an explicit flush to force any DB error (e.g. two checkpoints in
    this same part slugifying to the same source_key) to surface right
    there instead of silently poisoning the whole transaction at the final
    commit in _analyze_part. A single bad checkpoint is logged and skipped;
    it can never take the rest of the part's checkpoints down with it, and
    parsing itself is never failed by a skill-capture problem."""
    from app.services.skill_store import upsert_prompt_skill

    seen_titles: set[str] = set()
    for i, cp in enumerate(checkpoints):
        if cp.get("type") != "functional" or not cp.get("description"):
            continue
        title = (cp.get("title") or cp["description"][:80]).strip()
        dedup_key = title.lower()
        if dedup_key in seen_titles:
            # Two checkpoints in this batch would slugify to the same
            # source_key — disambiguate rather than letting the second
            # silently collide with the first (autoflush is off for this
            # session, so upsert_prompt_skill's lookup can't see the first
            # one's still-pending row within the same batch anyway).
            title = f"{title} ({i + 1})"
        seen_titles.add(dedup_key)

        try:
            with session.begin_nested():
                upsert_prompt_skill(
                    session,
                    title=title,
                    instruction=cp["description"],
                    source_type="sow",
                    artifact_id=artifact.id,
                    project_id=artifact.project_id,
                )
                session.flush()
        except Exception:
            logger.exception(
                "SOW ingest: failed to save skill for checkpoint %r of artifact %s "
                "— skipped, other checkpoints processed normally",
                title, artifact.id,
            )


@celery_app.task(
    name="sow_ingest.ingest_sow_task",
    bind=True,
    max_retries=0,
)
def ingest_sow_task(self, artifact_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, DesignRule, ParseStatus, SowPart
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
        except design_ingest.IngestError as exc:
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = str(exc)
            session.commit()
            logger.warning("SOW ingest: artifact %s failed: %s", artifact_id, exc)
            return

        chunks = design_ingest.chunk_text(text)
        parts = [
            SowPart(
                artifact_id=artifact.id,
                part_number=i + 1,
                content=chunk,
                char_count=len(chunk),
            )
            for i, chunk in enumerate(chunks)
        ]
        session.add_all(parts)
        artifact.total_parts = len(parts)

        if len(parts) == 1:
            # Small enough to need no chunking — analyze immediately, exactly
            # as today's single-shot behavior (upload -> processing -> done,
            # fully automatic, no user action needed).
            session.commit()
            _analyze_part(session, artifact, parts[0])
        else:
            # Large document — split into parts, nothing auto-starts. The
            # user triggers each part's analysis one at a time via the API.
            artifact.parse_status = ParseStatus.pending
            session.commit()
            logger.info(
                "SOW ingest: artifact %s split into %d parts, awaiting manual analysis",
                artifact_id,
                len(parts),
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


@celery_app.task(
    name="sow_ingest.analyze_sow_part_task",
    bind=True,
    max_retries=0,
)
def analyze_sow_part_task(self, artifact_id: str, part_number: int) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, ParseStatus, SowPart

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

        part = (
            session.query(SowPart)
            .filter(SowPart.artifact_id == artifact.id, SowPart.part_number == part_number)
            .one_or_none()
        )
        if part is None:
            logger.error("SOW ingest: artifact %s part %d not found", artifact_id, part_number)
            return

        # The API endpoint already flips this part to 'processing' (and
        # commits) before enqueueing, so seeing 'processing' here is the
        # expected normal case — do NOT treat it as already-in-flight-elsewhere.
        # Only a genuinely finished part should be skipped (e.g. a duplicate
        # task delivery arriving after the work is already done).
        if part.status == ParseStatus.done:
            logger.info(
                "SOW ingest: artifact %s part %d already done, skipping", artifact_id, part_number
            )
            return

        # Single-flight guard: never run two parts of the same document at once.
        other_active = (
            session.query(SowPart)
            .filter(
                SowPart.artifact_id == artifact.id,
                SowPart.part_number != part_number,
                SowPart.status == ParseStatus.processing,
            )
            .first()
        )
        if other_active is not None:
            logger.warning(
                "SOW ingest: artifact %s part %d requested while part %d is processing, skipping",
                artifact_id,
                part_number,
                other_active.part_number,
            )
            return

        _analyze_part(session, artifact, part)
    except Exception:
        logger.exception(
            "SOW ingest: unexpected failure analyzing artifact %s part %d",
            artifact_id,
            part_number,
        )
        session.rollback()
        try:
            part = (
                session.query(SowPart)
                .filter(SowPart.artifact_id == artifact_id, SowPart.part_number == part_number)
                .one_or_none()
            )
            if part is not None:
                part.status = ParseStatus.error
                part.error = "Unexpected worker failure — see worker logs."
                session.flush()
                artifact = (
                    session.query(DesignArtifact)
                    .filter(DesignArtifact.id == artifact_id)
                    .one_or_none()
                )
                if artifact is not None:
                    _recompute_artifact_status(session, artifact)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "SOW ingest: could not mark artifact %s part %d as errored",
                artifact_id,
                part_number,
            )
    finally:
        session.close()
