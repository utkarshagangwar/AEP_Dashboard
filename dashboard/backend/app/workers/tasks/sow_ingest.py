"""Celery tasks — ingest a SOW artifact into checkpoints (Phase 3, The Brain).

Memory Bank contract: if a design_rules row already exists for the artifact,
ingest_sow_task exits immediately — a document is never parsed (and never
costs tokens) twice. Every exception raised *within a live worker process*
is caught and written back as parse_status='error' + parse_error. That does
NOT cover the worker process itself dying mid-analysis (container restart,
OOM-kill, deploy) — a part can be left stuck 'processing' forever with no
exception ever raised to catch. app.workers.tasks.visual_qa_reconcile runs
periodically to detect and recover exactly that case.

Number of parts is a function of document length against
doc_chunking.DEFAULT_MAX_CHARS, but boundaries are chosen structurally
(headings, whole tables, whole code fences) rather than by character count
alone — see SOW_CHUNKING_PLAN.md. There is no cap on how many parts a
document can be split into.

Each SowPart also records where it came from: heading_path, locator,
strategy and the exact context_header sent to the LLM (migration 0038). A
part with strategy="hard_split" was cut at an arbitrary point because a
single unit exceeded the budget on its own; that is surfaced to the UI as
a degraded badge rather than left in the logs.

Large documents are split into sow_parts and analyzed one part at a time,
automatically: ingest_sow_task starts part 1 and each part chains the next
as it finishes (_chain_next_part). Only one part of a document is ever in
flight, so this is strictly a scheduling change — the single-flight guard in
analyze_sow_part_task is unchanged and still authoritative.

This replaced a manual model in which a multi-part document started nothing
at all and the user clicked Analyze once per part. On a document that splits
into a dozen-plus parts that reliably produced checkpoints and skills for
only the parts someone remembered to click, with no indication that the rest
existed. Set SOW_AUTO_ANALYZE_PARTS=0 to restore the manual behaviour; the
per-part API endpoint remains available either way as the retry path.

Checkpoints from every 'done' part are merged (concatenated by part_number)
into the artifact's single design_rules row after each part completes.
"""
import os

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Seconds between one part finishing and the next being enqueued. Not a
# correctness requirement — the single-flight guard in analyze_sow_part_task
# already serialises parts — but cheap insurance against a ~20-part document
# firing twenty back-to-back calls into a provider's per-minute rate limit.
_PART_CHAIN_DELAY_S = int(os.environ.get("SOW_PART_CHAIN_DELAY_S", "").strip() or 5)

# Stop auto-chaining after this many parts fail in a row. A document that is
# systematically unparseable (wrong file type, provider misconfigured) should
# cost a few calls to discover, not one per part.
_MAX_CONSECUTIVE_PART_FAILURES = 3


def _auto_analyze_enabled() -> bool:
    """Whether parts analyze themselves end-to-end. Opt-OUT, not opt-in.

    Multi-part documents previously started nothing at all: the user had to
    click Analyze once per part, so a large SOW silently produced skills for
    only the part they happened to click. Set SOW_AUTO_ANALYZE_PARTS=0 to
    restore the manual behaviour.
    """
    return (os.environ.get("SOW_AUTO_ANALYZE_PARTS", "").strip() or "1") not in (
        "0", "false", "False", "no",
    )


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


def _chain_next_part(session, artifact, current_part_number: int) -> None:
    """Enqueue the next unanalyzed part of this document, if any.

    This is what makes a multi-part document analyze itself. Chaining
    (each part enqueuing the next as it finishes) rather than fanning out
    all parts at once preserves the existing single-flight invariant —
    never two parts of the same document in flight — while removing the
    manual click that was leaving most of a large document unanalyzed.

    Chains after a FAILED part too, so one bad section doesn't strand
    everything after it, but stops once _MAX_CONSECUTIVE_PART_FAILURES
    parts have failed in a row: at that point the problem is the document
    or the provider, not the part, and continuing just burns calls.

    Best-effort: a scheduling failure is logged, never raised. The part
    that just completed is already committed, and the manual per-part
    endpoint remains available as the recovery path.
    """
    from app.models.visual_qa import ParseStatus, SowPart

    if not _auto_analyze_enabled():
        return

    try:
        ordered = (
            session.query(SowPart)
            .filter(SowPart.artifact_id == artifact.id)
            .order_by(SowPart.part_number)
            .all()
        )

        # Consecutive failures counted backwards from the part that just
        # finished — an earlier failure followed by successes is not a
        # systemic problem and must not halt the run.
        streak = 0
        for p in reversed([p for p in ordered if p.part_number <= current_part_number]):
            if p.status != ParseStatus.error:
                break
            streak += 1
        if streak >= _MAX_CONSECUTIVE_PART_FAILURES:
            artifact.parse_error = (
                f"Automatic analysis stopped after {streak} consecutive part failures. "
                "Fix the underlying problem and re-analyze the remaining parts manually."
            )
            session.commit()
            logger.error(
                "SOW ingest: artifact %s halted auto-analysis after %d consecutive "
                "part failures", artifact.id, streak,
            )
            return

        next_part = next(
            (
                p for p in ordered
                if p.part_number > current_part_number and p.status == ParseStatus.pending
            ),
            None,
        )
        if next_part is None:
            logger.info(
                "SOW ingest: artifact %s has no further pending parts — analysis complete",
                artifact.id,
            )
            return

        analyze_sow_part_task.apply_async(
            (str(artifact.id), next_part.part_number), countdown=_PART_CHAIN_DELAY_S
        )
        logger.info(
            "SOW ingest: artifact %s queued part %d/%d (auto-chained)",
            artifact.id, next_part.part_number, artifact.total_parts,
        )
    except Exception:  # noqa: BLE001 — chaining must never fail a completed part
        logger.exception(
            "SOW ingest: could not chain the next part after part %d of artifact %s",
            current_part_number, artifact.id,
        )


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

    # Prefer the stored context header (SOW_CHUNKING_PLAN Phase 3): it names
    # the document, the section path and the preceding-context tail, which is
    # what replaced the bare "part 3 of 7" label. Parts written before
    # migration 0038 have no header, so the old label is still built as a
    # fallback -- they must keep analysing correctly without re-ingestion.
    part_label = (
        f"part {part.part_number} of {artifact.total_parts}"
        if artifact.total_parts > 1
        else None
    )
    content = part.content
    if part.context_header:
        content = f"{part.context_header}\n\n<content>\n{part.content}\n</content>"
        part_label = None  # the header already states the part and section

    try:
        checkpoints, model_used = design_ingest.parse_sow(content, part_label=part_label)
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
        # Keep going: one unparseable section must not strand every part
        # after it. _chain_next_part halts on a run of failures.
        _chain_next_part(session, artifact, part.part_number)
        return

    part.checkpoints = checkpoints
    part.parsed_by_model = model_used
    part.status = ParseStatus.done
    # Same reason as above: flush before the helpers re-query SowPart rows.
    session.flush()
    _merge_checkpoints(session, artifact)
    _recompute_artifact_status(session, artifact)
    _save_functional_skills(session, artifact, checkpoints, part.part_number)
    session.commit()
    logger.info(
        "SOW ingest: artifact %s part %d/%d parsed into %d checkpoint(s) via %s",
        artifact.id,
        part.part_number,
        artifact.total_parts,
        len(checkpoints),
        model_used,
    )
    _chain_next_part(session, artifact, part.part_number)


def _save_functional_skills(session, artifact, checkpoints: list[dict], part_number: int) -> None:
    """Save every functional checkpoint from this part directly as a skill —
    a detailed prompt instruction, no live browser run required. Visual
    checkpoints (pixel-diff/appearance claims) have nothing to execute, so
    they're skipped.

    Requirements flagged for review deliberately do NOT become Skills/TDDs.
    A Vibe Testing skill is executable input; saving a known-incomplete or
    conflicting requirement makes the later run fail in a way that looks
    like a product defect. The checkpoint remains in the review queue with
    its reason, so the requirement is visible and can be clarified before a
    future extraction produces a runnable skill.

    Each checkpoint is saved in its own SAVEPOINT (session.begin_nested()),
    with an explicit flush to force any DB error (e.g. two checkpoints
    slugifying to the same source_key) to surface right there instead of
    silently poisoning the whole transaction at the final commit in
    _analyze_part. A single bad checkpoint is logged and skipped; it can
    never take the rest of the part's checkpoints down with it, and parsing
    itself is never failed by a skill-capture problem."""
    from app.services.skill_store import upsert_prompt_skill

    seen_titles: set[str] = set()
    for i, cp in enumerate(checkpoints):
        if cp.get("type") != "functional" or not cp.get("description"):
            continue
        if cp.get("review_status"):
            logger.info(
                "SOW ingest: held checkpoint %r from skill creation because it needs review: %s",
                cp.get("title") or "Untitled requirement",
                cp.get("review_reason") or "source details are incomplete or conflicting",
            )
            continue
        title = (cp.get("title") or cp["description"][:80]).strip()
        dedup_key = title.lower()
        if dedup_key in seen_titles:
            # Two checkpoints in this batch would slugify to the same
            # source_key — disambiguate rather than letting the second
            # silently collide with the first (autoflush is off for this
            # session, so upsert_prompt_skill's lookup can't see the first
            # one's still-pending row within the same batch anyway).
            #
            # The part number is in the suffix because source_key is unique
            # per ARTIFACT, not per part: with a document split into a dozen
            # parts, two different parts producing the same title (very
            # likely — "Search and Filter" appears in many sections) would
            # otherwise collide across parts, and the later part would
            # overwrite the earlier part's skill. A per-part suffix keeps
            # each one addressable while staying stable across re-analysis
            # of that same part.
            title = f"{title} (part {part_number}.{i + 1})"
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
                    review_status=cp.get("review_status"),
                    review_reason=cp.get("review_reason"),
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
            # Structure-aware chunking (SOW_CHUNKING_PLAN Phase 3) needs the
            # block list; the flat text is no longer read here at all.
            blocks = design_ingest.extract_blocks(
                artifact.storage_path, artifact.file_name
            )
        except design_ingest.IngestError as exc:
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = str(exc)
            session.commit()
            logger.warning("SOW ingest: artifact %s failed: %s", artifact_id, exc)
            return

        from app.services.doc_chunking import chunk_document

        chunks = chunk_document(
            blocks,
            file_name=artifact.file_name,
            doc_kind="checkpoints_sow",
            document_title=artifact.file_name,
        )
        parts = [
            SowPart(
                artifact_id=artifact.id,
                part_number=chunk.index,
                content=chunk.text,
                char_count=chunk.char_count,
                # Chunk provenance -- see migration 0038. heading_path is what
                # lets a reviewer see which section a part covers without
                # reading it, and strategy="hard_split" is the degradation
                # signal the UI surfaces.
                heading_path=chunk.heading_path or None,
                locator=chunk.locator,
                strategy=chunk.strategy,
                context_header=chunk.context_header or None,
            )
            for chunk in chunks
        ]
        session.add_all(parts)
        artifact.total_parts = len(parts)

        if not _auto_analyze_enabled() and len(parts) > 1:
            # Explicit opt-out only. Multi-part documents used to land here
            # unconditionally, which meant a large SOW produced checkpoints
            # and skills for none of its parts until the user clicked each
            # one — the reason most of a big document's skills were missing.
            artifact.parse_status = ParseStatus.pending
            session.commit()
            logger.info(
                "SOW ingest: artifact %s split into %d parts, awaiting manual analysis "
                "(SOW_AUTO_ANALYZE_PARTS is off)",
                artifact_id,
                len(parts),
            )
            return

        if len(parts) == 1:
            # Single part: analyze inline, exactly as before — no queue
            # round-trip for the common small-document case.
            session.commit()
            _analyze_part(session, artifact, parts[0])
        else:
            # Multi-part: start part 1 and let each part chain the next.
            artifact.parse_status = ParseStatus.pending
            session.commit()
            analyze_sow_part_task.apply_async((str(artifact.id), parts[0].part_number))
            logger.info(
                "SOW ingest: artifact %s split into %d parts, auto-analysis started",
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
            # Still chain: a redelivered duplicate task arriving after the
            # work finished would otherwise break the chain here and leave
            # every later part unanalyzed forever.
            _chain_next_part(session, artifact, part_number)
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
                if artifact is not None:
                    # An unexpected crash on one part must not strand the
                    # rest of the document either.
                    _chain_next_part(session, artifact, part_number)
        except Exception:  # noqa: BLE001
            logger.exception(
                "SOW ingest: could not mark artifact %s part %d as errored",
                artifact_id,
                part_number,
            )
    finally:
        session.close()
