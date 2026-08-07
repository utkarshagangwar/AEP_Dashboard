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
            # Only imported documents carry this (the chunker's own structural
            # knowledge of which heading the fact sat under). Transcripts,
            # recordings and images have no document outline, so it stays null
            # and grouping falls back to the LLM pass.
            source_heading_path=f.get("source_heading_path"),
        )
        for f in facts
    ]
    session.add_all(rows)
    return len(rows)


def _finish_source(session, source, *, facts_saved: int, failures: list[str]) -> None:
    """Write a source's terminal state, distinguishing a clean run from a
    partial one.

    A partially-extracted source must never look identical to a fully
    extracted one -- that is the whole justification for saving partial
    results at all (see sow_ledger._extract_chunks). `done_with_errors`
    carries the failing parts by name so the user knows exactly what to
    retry.
    """
    from app.models.sow import SowSourceStatus

    source.ledger_fact_count = facts_saved
    _clear_progress(source)
    if failures:
        source.status = SowSourceStatus.done_with_errors
        summary = "; ".join(failures[:5]) + (" …" if len(failures) > 5 else "")
        source.error_message = (
            f"{len(failures)} part(s) could not be extracted — {summary}. "
            f"{facts_saved} fact(s) from the remaining parts were saved; "
            "re-upload this source to retry the failed parts."
        )
    else:
        source.status = SowSourceStatus.done
        source.error_message = None


def _clear_prior_facts(session, document_id, artifact_id) -> None:
    """Delete this source's previously-extracted facts before re-running --
    a Retry must replace stale facts, never append duplicates alongside
    them."""
    from app.models.sow import SowRequirementsLedger

    session.query(SowRequirementsLedger).filter(
        SowRequirementsLedger.document_id == document_id,
        SowRequirementsLedger.source_artifact_id == artifact_id,
    ).delete(synchronize_session=False)


def _set_progress(session, source, stage: str, current: int, total: int) -> None:
    """Write one progress checkpoint for a source and commit it immediately.

    Committed on its own (rather than batched with the terminal write) on
    purpose -- the whole point is that the API's 3s poll can observe it
    while the worker is still running. Cheap: at most one small UPDATE per
    chunk, and chunk count is bounded by document size.

    Raises nothing meaningful to the caller by contract -- the service layer
    wraps every invocation in sow_ledger._report, which swallows failures so
    a progress write can never lose completed extraction work.
    """
    source.progress_stage = stage
    source.progress_current = current
    source.progress_total = total
    session.commit()


def _progress_writer(session, source):
    """Bind a source to a callback matching sow_ledger._report's contract."""
    def _cb(stage: str, current: int, total: int) -> None:
        _set_progress(session, source, stage, current, total)

    return _cb


def _clear_progress(source) -> None:
    """Drop progress on a terminal transition. Left behind, a finished
    source would keep rendering "part 12 of 12" next to a Done badge, and an
    errored one would show a bar frozen at whatever chunk failed."""
    source.progress_stage = None
    source.progress_current = None
    source.progress_total = None


_BASELINE_PROVENANCE = "imported verbatim (no AI)"


def _build_baseline_version(session, document, blocks) -> int:
    """Make the imported document itself version 1, verbatim.

    An imported SOW is the deliverable, not raw material to synthesise from.
    Building the version straight from its own blocks means the user sees
    their document — their wording, their structure, their section
    numbering — instead of an LLM's re-write of it, and it makes the
    skills/TDD extractor reachable immediately, without first pressing
    "Generate SOW" on a SOW that already exists.

    Only ever creates the FIRST version. If the document already has one,
    this returns 0 and changes nothing: a later import is new source
    material for an existing SOW, which belongs to the impact/rewrite path
    where the user chooses what to change. Silently replacing a version
    here could discard hand edits.

    `generated_by_model` records the provenance explicitly so a verbatim
    baseline is never mistaken for AI-drafted prose.

    Returns the number of sections created. Does not commit.
    """
    from app.models.sow import (
        SowDocumentStatus,
        SowDocumentVersion,
        SowSection,
        SowSectionStatus,
        SowVersionKind,
        SowVersionStatus,
    )
    from app.services.sow_baseline import build_sections_from_document

    if document.current_version_id is not None:
        return 0

    sections = build_sections_from_document(blocks)
    if not sections:
        logger.warning(
            "SOW baseline: document %s produced no renderable sections — leaving it "
            "without a version rather than creating an empty one", document.id,
        )
        return 0

    version = SowDocumentVersion(
        document_id=document.id,
        version_number=1,
        kind=SowVersionKind.full_generation,
        status=SowVersionStatus.done,
        generated_by_model=_BASELINE_PROVENANCE,
    )
    session.add(version)
    session.flush()  # populate version.id for the section FKs below

    for index, spec in enumerate(sections):
        session.add(SowSection(
            version_id=version.id,
            order_index=index,
            heading=spec["heading"],
            section_key=spec["section_key"],
            content_blocks=spec["content_blocks"],
            status=SowSectionStatus.done,
            # coverage_score stays null on purpose: coverage measures how
            # well DRAFTED prose covers the ledger. This section IS the
            # source document, so there is nothing to score it against.
            coverage_score=None,
        ))

    document.current_version_id = version.id
    document.status = SowDocumentStatus.ready
    logger.info(
        "SOW baseline: document %s -> version 1 with %d verbatim section(s)",
        document.id, len(sections),
    )
    return len(sections)


def _queue_impact_analysis(source_id: str) -> None:
    """Ask which already-drafted sections this new source affects.

    Only meaningful once a SOW has actually been generated: before that
    there are no sections to compare against, and the user's next step is
    Generate anyway. Analysis is *advisory* — it records the affected
    section keys so the UI can pre-tick them in the Rewrite dialog. Nothing
    is redrafted without the user pressing the button, so this never spends
    drafting tokens behind their back.

    Best-effort by contract: a failure here must not turn a successful
    extraction into an errored source, so it is logged and swallowed.
    """
    try:
        from app.workers.tasks.sow_impact import analyze_source_impact_task

        analyze_source_impact_task.delay(source_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "SOW ledger: could not queue impact analysis for source %s (ignored)",
            source_id, exc_info=True,
        )


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
            _clear_progress(source)
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
            # "reading" covers file I/O + text extraction, which has no
            # divisible unit -- total 0 tells the UI to render a stage
            # label rather than a fabricated percentage.
            _set_progress(session, source, "reading", 0, 0)
            text = extract_text(artifact.storage_path, artifact.file_name)
            facts, model_used, failures = sow_ledger.extract_ledger_from_transcript(
                text,
                document_title=artifact.file_name,
                on_progress=_progress_writer(session, source),
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            _clear_progress(source)
            session.commit()
            logger.warning("SOW ledger: transcript source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        _finish_source(session, source, facts_saved=count, failures=failures)
        session.commit()
        _queue_impact_analysis(source_id)
        logger.info(
            "SOW ledger: transcript source %s -> %d fact(s) via %s (%d failed part(s))",
            source_id, count, model_used, len(failures),
        )
    except Exception:
        logger.exception("SOW ledger: unexpected failure for transcript source %s", source_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(source_id)
        return
    finally:
        session.close()


@celery_app.task(name="sow_ledger.extract_existing_sow_ledger_task", bind=True, max_retries=0)
def extract_existing_sow_ledger_task(self, source_id: str) -> None:
    """Import SOW (SOW tab): extract ledger facts from an uploaded
    pre-existing SOW/requirements document (.docx/.pdf/.txt/.md). Mirrors
    extract_transcript_ledger_task exactly, except extraction goes through
    sow_import.extract_existing_sow_blocks (adds .docx support, and returns
    STRUCTURE rather than flat text so the chunker can split on real section
    boundaries) and the LLM pass uses a prompt tuned for "existing document"
    rather than "meeting transcript"
    (sow_ledger.extract_ledger_from_sow_document_full).

    Failure semantics changed with SOW_CHUNKING_PLAN Phase 4: if ANY chunk
    of the document fails extraction after retries, the whole source is
    marked error and no facts are saved. Previously a failed chunk was
    logged and skipped, so a partial ledger could silently become the SOW
    baseline.
    """
    from app.core.database import SessionLocal
    from app.models.sow import SowDocumentSource, SowSourceStatus
    from app.services import sow_ledger
    from app.services.design_ingest import IngestError
    from app.services.sow_import import extract_existing_sow_blocks

    session = SessionLocal()
    try:
        from app.models.visual_qa import DesignArtifact

        source = session.get(SowDocumentSource, source_id)
        if source is None or source.artifact_id is None:
            logger.error("SOW ledger: existing-SOW source %s not found", source_id)
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
            # Blocks, not flat text (SOW_CHUNKING_PLAN Phase 3): the chunker
            # needs heading levels, table boundaries and page markers to
            # split on real section boundaries and to label each part with
            # the section it came from. Passing a string here still works
            # but silently degrades to paragraph splitting.
            _set_progress(session, source, "reading", 0, 0)
            blocks = extract_existing_sow_blocks(artifact.storage_path, artifact.file_name)
            (
                facts, model_used, failures, outline,
            ) = sow_ledger.extract_ledger_from_sow_document_full(
                blocks,
                file_name=artifact.file_name,
                document_title=artifact.file_name,
                on_progress=_progress_writer(session, source),
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            _clear_progress(source)
            session.commit()
            logger.warning("SOW ledger: existing-SOW source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        # The imported document's own table of contents, kept so a later
        # regeneration can reproduce its section order instead of inventing
        # a new one (see sow_drafting.group_ledger_into_sections).
        source.source_outline = outline or None
        _finish_source(session, source, facts_saved=count, failures=failures)

        # The document itself becomes version 1, verbatim. Done in the SAME
        # transaction as the facts so the two can never disagree about
        # whether this import succeeded.
        from app.models.sow import SowDocument

        document = session.get(SowDocument, source.document_id)
        baseline_sections = 0
        if document is not None:
            baseline_sections = _build_baseline_version(session, document, blocks)

        session.commit()

        # Impact analysis answers "which EXISTING sections does this NEW
        # source affect". When baseline_sections > 0 this source just BECAME
        # version 1 — the sections and the facts are the same document — so
        # running it would diff the import against itself and report every
        # section as affected by brand-new material. baseline_sections == 0
        # means either a version already existed (this really is new material
        # for it — analyse) or no version could be built (the task self-guards
        # on current_version_id).
        if baseline_sections > 0:
            logger.info(
                "SOW ledger: existing-SOW source %s became the verbatim baseline — "
                "skipping impact analysis (nothing pre-existing to affect)", source_id,
            )
        else:
            _queue_impact_analysis(source_id)

        logger.info(
            "SOW ledger: existing-SOW source %s -> %d fact(s), %d outline heading(s), "
            "%d baseline section(s) via %s (%d failed part(s))",
            source_id, count, len(outline), baseline_sections, model_used, len(failures),
        )
    except Exception:
        logger.exception("SOW ledger: unexpected failure for existing-SOW source %s", source_id)
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
            # A recording is ONE Gemini call (upload + preprocess + generate)
            # with no chunk boundary to count, so it reports a stage only --
            # total 0. Deliberately not a fake timer-driven percentage: a bar
            # that reaches 99% and sits there is worse than no bar.
            _set_progress(session, source, "extracting", 0, 0)
            facts, model_used = sow_ledger.extract_ledger_from_recording(
                artifact.storage_path,
                artifact.file_name,
                context_label=artifact.platform_name,
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            _clear_progress(source)
            session.commit()
            logger.warning("SOW ledger: recording source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        # A recording is one indivisible call — it either produced facts or
        # raised, so there is no partial-failure list to report.
        _finish_source(session, source, facts_saved=count, failures=[])
        session.commit()
        _queue_impact_analysis(source_id)
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
            _set_progress(session, source, "reading", 0, 0)
            with open(artifact.storage_path, "rb") as fh:
                image_bytes = fh.read()
        except OSError as exc:
            source.status = SowSourceStatus.error
            source.error_message = f"Could not read design file: {exc}"
            _clear_progress(source)
            session.commit()
            logger.warning("SOW ledger: design source %s file read failed: %s", source_id, exc)
            return

        try:
            # Single vision call, same rationale as the recording task above.
            _set_progress(session, source, "extracting", 0, 0)
            facts, model_used = sow_ledger.extract_ledger_from_image(
                image_bytes, artifact.file_name, context_label=artifact.target_page
            )
        except IngestError as exc:
            source.status = SowSourceStatus.error
            source.error_message = str(exc)
            _clear_progress(source)
            session.commit()
            logger.warning("SOW ledger: design source %s failed: %s", source_id, exc)
            return

        _clear_prior_facts(session, source.document_id, source.artifact_id)
        session.flush()
        count = _save_facts(session, source.document_id, source.artifact_id, facts)
        # One vision call, same as the recording task — no partial state.
        _finish_source(session, source, facts_saved=count, failures=[])
        session.commit()
        _queue_impact_analysis(source_id)
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
