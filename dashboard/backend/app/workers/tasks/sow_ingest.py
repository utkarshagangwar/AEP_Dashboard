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
from app.services import ai_usage
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


def _merge_checkpoints(session, artifact) -> dict[int, set[int]]:
    """Recompute the artifact's DesignRule from every 'done' part, merging
    duplicates across parts (tdd_extraction Stage 6).

    A SOW almost always describes a feature twice — once in a summary section,
    once in detail — and those land in different parts. Per-part extraction
    cannot see that, so before this the document list carried both copies and
    each produced its own Skill.

    Returns absorbed: part_number -> indices of that part's checkpoints that
    were merged into an earlier part's. The caller uses it to skip creating
    duplicate Skills. Each SowPart.checkpoints is deliberately left untouched:
    it stays the record of what that section actually produced, and the
    merging is a document-level view over it.
    """
    from app.models.visual_qa import DesignRule, ParseStatus, SowPart
    from app.services import tdd_extraction

    done_parts = (
        session.query(SowPart)
        .filter(SowPart.artifact_id == artifact.id, SowPart.status == ParseStatus.done)
        .order_by(SowPart.part_number)
        .all()
    )
    models_used: list[str] = []
    for p in done_parts:
        if p.parsed_by_model and p.parsed_by_model not in models_used:
            models_used.append(p.parsed_by_model)

    # The naming-consolidation call runs once, when the last part lands —
    # a 12-part document pays for one call, not twelve, and names that are
    # still arriving are not consolidated against. Merging itself is
    # deterministic and happens on every call regardless.
    reconciled = tdd_extraction.reconcile_across_parts(
        [{"part_number": p.part_number, "checkpoints": p.checkpoints or []} for p in done_parts],
        finalize=len(done_parts) >= (artifact.total_parts or 1),
    )
    checkpoints = reconciled.checkpoints
    if reconciled.model_used and reconciled.model_used not in models_used:
        models_used.append(reconciled.model_used)

    rule = session.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
    if rule is None:
        rule = DesignRule(artifact_id=artifact.id, checkpoints=checkpoints)
        session.add(rule)
    rule.checkpoints = checkpoints
    rule.parsed_by_model = ", ".join(models_used) if models_used else None
    return reconciled.absorbed


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
    from app.services import design_ingest, tdd_extraction

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

    # The project's UI naming reference, so generated instructions name the
    # product's real controls instead of the document's wording for them (see
    # app.services.ui_inventory). Built once per project and cached, so this
    # costs one vision call for the first part of the first SOW and nothing
    # afterwards. Returns None — and extraction proceeds on text alone — for
    # an artifact with no project, with no evidence uploaded, or when the
    # vision call fails.
    from app.services import (
        flow_validation,
        sow_progress,
        ui_inventory as ui_inventory_service,
    )

    progress = sow_progress.reporter(artifact.id, part.part_number)
    if artifact.total_parts and artifact.total_parts > 1:
        progress(
            "part", sow_progress.RUNNING,
            f"Reading part {part.part_number} of {artifact.total_parts}",
            {"part": part.part_number, "total": artifact.total_parts},
        )

    ui_inventory = ui_inventory_service.get_inventory_text(session, artifact.project_id)
    # The project's execution flow, so a test is anchored to a state a tester
    # can reach rather than to a document section. Returns None for every
    # project until the flow layer supplies one, and a None flow model makes
    # the whole stage a no-op — this line is safe to ship before that exists.
    flow_model = flow_validation.get_flow_model(session, artifact.project_id)
    # Reported per part rather than once, because it is the answer to "will
    # this test say Apply Now or Submit Application" and that answer applies
    # to every part. Absence is stated, not left silent: a reader who cannot
    # see that no reference was used has no way to explain wrong labels later.
    if ui_inventory:
        progress(
            "naming_reference", sow_progress.DONE,
            f"Using the project's UI naming reference "
            f"({ui_inventory.count(chr(10) + '- ') + 1} screens) so tests name real controls",
        )
    else:
        progress(
            "naming_reference", sow_progress.SKIPPED,
            "No UI naming reference for this project — tests will name controls "
            "the way the document does",
        )

    try:
        # parse_sow_detailed (not parse_sow) so the testability gate's
        # exclusions and the coverage scorecard can be persisted on the part.
        # Losing them would make the gate unauditable — see SowPart.excluded_zones.
        extraction = design_ingest.parse_sow_detailed(
            content,
            part_label=part_label,
            ui_inventory=ui_inventory,
            flow_model=flow_model,
            on_progress=progress,
        )
        checkpoints, model_used = extraction.checkpoints, extraction.model_used
    except design_ingest.IngestError as exc:
        # The failure is a progress event too. A panel that simply stops
        # updating is indistinguishable from one whose worker died, and the
        # reader is left watching a spinner that will never resolve.
        progress("extract", sow_progress.ERROR, f"Extraction failed: {exc}")
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
    part.excluded_zones = extraction.excluded_zones or None
    part.coverage_json = extraction.coverage or None
    part.parsed_by_model = model_used
    part.status = ParseStatus.done
    # Same reason as above: flush before the helpers re-query SowPart rows.
    session.flush()
    absorbed = _merge_checkpoints(session, artifact)
    _recompute_artifact_status(session, artifact)
    # Skip the checkpoints this part restated from an earlier one: the Skill
    # already exists, created by the part that stated it first. Without this
    # the document list would be reconciled while the Skills tab still showed
    # both copies — which is the half of the problem the user actually sees.
    skipped = absorbed.get(part.part_number) or set()
    if skipped:
        progress(
            "reconcile", sow_progress.DONE,
            f"Merged {len(skipped)} test{'' if len(skipped) == 1 else 's'} that "
            "an earlier part already covered",
            {"merged": len(skipped)},
        )
    saved = _save_functional_skills(
        session, artifact, checkpoints, part.part_number,
        skip_indices=skipped,
    )
    session.commit()
    # Emitted after the commit that persists them: an event claiming skills
    # exist before the transaction that creates them has landed would be a
    # promise the database has not yet made.
    progress(
        "skills", sow_progress.DONE,
        f"Saved {saved} runnable skill{'' if saved == 1 else 's'} to Vibe Testing",
        {"skills": saved},
    )
    # Closes the RUNNING "Reading part N of M" above -- same emit-don't-edit
    # reasoning as the read stage in _run_sow_ingest, and guarded on the same
    # condition that opened it so the pair can never be half-emitted.
    #
    # Deliberately only on the success path. The failure path returns early
    # above, having emitted its own `extract`/ERROR event for this part; the
    # panel resolves a group whose child errored to error, so the header still
    # stops spinning without a second error row restating the same failure.
    if artifact.total_parts and artifact.total_parts > 1:
        progress(
            "part", sow_progress.DONE,
            f"Finished part {part.part_number} of {artifact.total_parts}",
            {"part": part.part_number, "total": artifact.total_parts},
        )
    coverage = extraction.coverage or {}
    logger.info(
        "SOW ingest: artifact %s part %d/%d parsed into %d checkpoint(s) "
        "(neg+edge ratio %.2f, %d zone(s) excluded as non-testable) via %s",
        artifact.id,
        part.part_number,
        artifact.total_parts,
        len(checkpoints),
        coverage.get("negative_edge_ratio", 0.0),
        len(extraction.excluded_zones or []),
        model_used,
    )
    # Extraction QUALITY gate (TDD_EXTRACTION_SPEC.md §10). Distinct from the
    # error path above, which catches extraction FAILING. This catches
    # extraction succeeding while producing the wrong shape of output —
    # happy-path-only checkpoints, the original defect — which is otherwise
    # invisible until someone reads every generated skill. Warn only: the
    # checkpoints are kept and the part is still `done`, because a thin
    # section can legitimately score low and discarding real requirements over
    # a heuristic would be far worse than a noisy log line.
    gate_warning = tdd_extraction.ratio_gate_warning(extraction.coverage)
    if gate_warning:
        logger.warning(
            "SOW ingest: artifact %s part %d/%d — %s",
            artifact.id,
            part.part_number,
            artifact.total_parts,
            gate_warning,
        )
    _chain_next_part(session, artifact, part.part_number)


def _save_functional_skills(
    session,
    artifact,
    checkpoints: list[dict],
    part_number: int,
    skip_indices: set[int] | None = None,
) -> int:
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
    itself is never failed by a skill-capture problem.

    A behaviour now produces SEVERAL checkpoints (positive / negative / edge,
    per app.services.tdd_extraction), and each becomes its own skill: a
    negative case is a separate runnable test, not a footnote on the happy
    path. They stay linked by behaviour_key so the Skills tab can group them.

    Checkpoints with grounding="derived" — the negative/edge cases inferred
    from standard QA practice rather than stated in the document — become
    skills by default, because a suite with no negative coverage is the
    problem this pipeline exists to fix. Set TDD_DERIVED_AS_SKILLS=0 to hold
    them back for review instead; they remain in the checkpoint list either
    way."""
    from app.services.skill_store import upsert_prompt_skill
    from app.services.tdd_extraction import derived_as_skills

    allow_derived = derived_as_skills()
    skip_indices = skip_indices or set()
    seen_titles: set[str] = set()
    saved = 0
    for i, cp in enumerate(checkpoints):
        if cp.get("type") != "functional" or not cp.get("description"):
            continue
        if i in skip_indices:
            # Cross-part reconciliation matched this to a checkpoint an
            # earlier part already produced. The requirement is not lost —
            # its Skill exists, and the document-level checkpoint records
            # this part in merged_from_parts.
            logger.info(
                "SOW ingest: checkpoint %r in part %d restates one from an "
                "earlier part — existing skill kept, no duplicate created",
                cp.get("title") or "Untitled requirement", part_number,
            )
            continue
        if cp.get("review_status"):
            logger.info(
                "SOW ingest: held checkpoint %r from skill creation because it needs review: %s",
                cp.get("title") or "Untitled requirement",
                cp.get("review_reason") or "source details are incomplete or conflicting",
            )
            continue
        if cp.get("grounding") == "derived" and not allow_derived:
            logger.info(
                "SOW ingest: held derived checkpoint %r from skill creation "
                "(TDD_DERIVED_AS_SKILLS is disabled)",
                cp.get("title") or "Untitled requirement",
            )
            continue
        title = (cp.get("title") or cp["description"][:80]).strip()
        # Include the test type in the identity: "Create Job" positive and
        # "Create Job" negative are two different tests, and a model that
        # titles both of them identically must not have one overwrite the
        # other via the shared source_key.
        dedup_key = f"{title.lower()}|{cp.get('test_type') or ''}"
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
                    test_type=cp.get("test_type"),
                    category=cp.get("category"),
                    grounding=cp.get("grounding"),
                    behaviour_key=cp.get("behaviour_key"),
                    priority=cp.get("priority"),
                )
                session.flush()
            # Counted only after the flush succeeds. Counting the attempt
            # would let the progress panel report skills that a SAVEPOINT
            # then rolled back — the panel must not be more optimistic than
            # the database.
            saved += 1
        except Exception:
            logger.exception(
                "SOW ingest: failed to save skill for checkpoint %r of artifact %s "
                "— skipped, other checkpoints processed normally",
                title, artifact.id,
            )
    return saved


@celery_app.task(
    name="sow_ingest.ingest_sow_task",
    bind=True,
    max_retries=0,
)
@ai_usage.tracked_task("sow_import")
def ingest_sow_task(self, artifact_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, DesignRule, ParseStatus, SowPart
    from app.services import design_ingest, sow_progress

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

        # Fresh run, fresh timeline. A retried artifact would otherwise show
        # the failed attempt's steps stacked above the new ones with nothing
        # marking the boundary, which reads as one very confused run.
        sow_progress.clear(artifact.id)
        progress = sow_progress.reporter(artifact.id)
        progress(
            "read", sow_progress.RUNNING,
            f"Reading {artifact.file_name}",
        )

        try:
            # Structure-aware chunking (SOW_CHUNKING_PLAN Phase 3) needs the
            # block list; the flat text is no longer read here at all.
            blocks = design_ingest.extract_blocks(
                artifact.storage_path, artifact.file_name
            )
        except design_ingest.IngestError as exc:
            progress("read", sow_progress.ERROR, f"Could not read the document: {exc}")
            artifact.parse_status = ParseStatus.error
            artifact.parse_error = str(exc)
            session.commit()
            logger.warning("SOW ingest: artifact %s failed: %s", artifact_id, exc)
            return

        # Closes the RUNNING event above.
        #
        # A stage is finished by EMITTING its completion, never by editing the
        # row that opened it: the panel polls
        # visual_audit.get_sow_progress with `?after=<last sequence seen>`, so
        # it only ever receives rows it has not read yet. An in-place update to
        # an earlier row would be invisible to every client already past that
        # sequence -- which is every client that saw the stage start.
        #
        # The panel folds this onto the opening row rather than rendering both,
        # so the timeline still reads "Reading <file>" once, ticked. See
        # SowExtractionProgress.buildGroups.
        progress("read", sow_progress.DONE, f"Read {artifact.file_name}")

        from app.services.doc_chunking import STRATEGY_HARD_SPLIT, chunk_document

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
        # Degraded chunks are named here rather than only in the log: a part
        # cut mid-paragraph extracts worse, and the reader deserves to know
        # that before wondering why one section produced weak tests.
        degraded = sum(1 for c in chunks if c.strategy == STRATEGY_HARD_SPLIT)
        progress(
            "chunk", sow_progress.DONE,
            f"Read the document and split it into {len(parts)} part"
            f"{'' if len(parts) == 1 else 's'}"
            + (f" ({degraded} had to be cut mid-section)" if degraded else ""),
            {"parts": len(parts), "degraded": degraded},
        )

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
@ai_usage.tracked_task("sow_import")
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
