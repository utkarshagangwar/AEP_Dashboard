"""Celery task — generate a full SOW version from a document's requirements
ledger. Pass 2 grouping + drafting (Phase 3), Pass 3 completeness audit
(Phase 4 — app.services.sow_audit, independent per-section LLM check, not
the same call that drafted it), Pass 4 assembly (Phase 3).

Lifecycle: the API endpoint (app/api/v1/sow.py::generate_document) creates
the SowDocumentVersion (status='pending') and SowGenerationJob
(status='queued') rows and sets SowDocument.status='generating' — all
inside one transaction, with a row lock guarding against a second
concurrent generate/rewrite call (plan §11.3) — then enqueues this task
AFTER committing, same "enqueue after commit" convention used everywhere
else in this codebase (sow_ingest.py, video_ingest.py) so the worker can
always load the row.

Partial-failure model (plan §11.5): this is NOT one atomic pass/fail unit.
Each section is drafted independently and can fail on its own without
discarding every other section that succeeded — the version only becomes
'error' if EVERY section failed; otherwise 'done_with_errors' with the
failed sections individually marked and retryable later (retry endpoint:
Phase 4+, not built yet — for now a failed section is visible with its
error message, not silently absent).
"""
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _mark_unexpected_failure(document_id: str, version_id: str) -> None:
    """Best-effort error-state recovery for the outer except block — opens
    its own fresh session since the one in scope at the point of failure
    may itself be poisoned."""
    from app.core.database import SessionLocal
    from app.models.sow import SowDocument, SowDocumentStatus, SowDocumentVersion, SowVersionStatus

    session = SessionLocal()
    try:
        version = session.get(SowDocumentVersion, version_id)
        if version is not None:
            version.status = SowVersionStatus.error
            version.error_message = "Unexpected worker failure — see worker logs."
        document = session.get(SowDocument, document_id)
        if document is not None:
            document.status = SowDocumentStatus.error
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "SOW generation: could not mark document %s / version %s as errored",
            document_id, version_id,
        )
    finally:
        session.close()


@celery_app.task(
    name="sow_generation.generate_sow_task",
    bind=True,
    max_retries=0,
    # A full generation is N+2 sequential LLM calls (grouping + overview +
    # one per functional section) — bounded well above a single call's own
    # VISUAL_LLM_TIMEOUT_S but still short of the global 1800s default, same
    # reasoning as every other multi-call SOW/video task in this codebase.
    soft_time_limit=1200,
)
def generate_sow_task(self, document_id: str, version_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.sow import (
        SowDocument,
        SowDocumentStatus,
        SowDocumentVersion,
        SowGenerationJob,
        SowJobStage,
        SowJobStatus,
        SowRequirementsLedger,
        SowSection,
        SowSectionStatus,
        SowVersionStatus,
    )
    from app.services import sow_assembly, sow_drafting, sow_patch
    from app.services.design_ingest import IngestError

    session = SessionLocal()
    try:
        document = session.get(SowDocument, document_id)
        version = session.get(SowDocumentVersion, version_id)
        if document is None or version is None:
            logger.error(
                "SOW generation: document %s or version %s not found", document_id, version_id
            )
            return
        job = (
            session.query(SowGenerationJob)
            .filter(SowGenerationJob.version_id == version.id)
            .order_by(SowGenerationJob.created_at.desc())
            .first()
        )
        if job is None:
            logger.error("SOW generation: no job row for version %s", version_id)
            return

        def _update_job(*, stage=None, status=None, progress=None):
            if stage is not None:
                job.stage = stage
            if status is not None:
                job.status = status
            if progress is not None:
                job.stage_progress = progress
            session.commit()

        # Idempotency guard: task_acks_late=True (celery_app.py) means a
        # worker crash/restart mid-task gets this task redelivered and
        # re-run from scratch with the same document_id/version_id. Any
        # section rows a crashed prior attempt already inserted for this
        # version must be cleared first, or a redelivered run would
        # duplicate every section rather than cleanly redoing the work.
        # Mirrors sow_ledger.py's _clear_prior_facts pattern for the same
        # class of problem on the retry-a-source path.
        session.query(SowSection).filter(SowSection.version_id == version.id).delete()
        session.commit()

        job.started_at = datetime.now(timezone.utc)
        _update_job(
            stage=SowJobStage.ledger_extraction,
            status=SowJobStatus.running,
            progress="Loading requirements ledger",
        )

        facts = (
            session.query(SowRequirementsLedger)
            .filter(
                SowRequirementsLedger.document_id == document.id,
                SowRequirementsLedger.superseded.is_(False),
            )
            .order_by(SowRequirementsLedger.created_at.asc())
            .all()
        )

        if not facts:
            msg = (
                "No requirements ledger facts found — attach at least one source and "
                "wait for extraction to finish before generating."
            )
            version.status = SowVersionStatus.error
            version.error_message = msg
            document.status = SowDocumentStatus.error
            job.status = SowJobStatus.error
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            logger.warning("SOW generation: version %s has no ledger facts, aborted", version_id)
            return

        # ── Pass 2a: grouping ────────────────────────────────────────────
        _update_job(stage=SowJobStage.drafting, progress="Grouping requirements into sections")
        try:
            groups, _ = sow_drafting.group_ledger_into_sections(facts)
        except IngestError as exc:
            # Grouping failure degrades to "one big section" rather than
            # aborting the whole generation — every fact still gets
            # drafted, just without a useful heading breakdown.
            logger.warning(
                "SOW generation: grouping failed for version %s (%s) — falling back to a "
                "single ungrouped section", version_id, exc,
            )
            groups = [{
                "heading": "Requirements",
                "section_key": "requirements",
                "fact_indices": list(range(len(facts))),
            }]

        # Stamp every grouped fact with the section_key it was assigned to
        # (plan §11.4) -- this is what lets Phase 7's patch_sow_task later
        # find "which facts belong to this section" without re-running
        # grouping. Set regardless of whether that section's drafting call
        # later succeeds or fails below: the ASSIGNMENT is a Pass 2a
        # result, independent of whether Pass 2b's prose-writing for it
        # worked, and a failed section can still be retried via a Phase 7
        # patch using these same facts.
        for group in groups:
            for idx in group["fact_indices"]:
                facts[idx].assigned_section_key = group["section_key"]

        order_index = 0
        sections_created: list[SowSection] = []
        models_used: set[str] = set()

        def _add_section(
            heading, section_key, content_blocks, status, error_message=None, model=None,
            coverage_score=None, coverage_gaps=None,
        ):
            nonlocal order_index
            sec = SowSection(
                version_id=version.id,
                order_index=order_index,
                heading=heading,
                section_key=section_key,
                content_blocks=content_blocks or [],
                status=status,
                error_message=error_message,
                coverage_score=coverage_score,
                coverage_gaps=coverage_gaps,
            )
            session.add(sec)
            order_index += 1
            sections_created.append(sec)
            if model:
                models_used.add(model)
            return sec

        # ── Framing sections (Overview + Scope of Work) ─────────────────
        _update_job(progress="Drafting project overview")
        try:
            overview_blocks, scope_blocks, model_used = sow_assembly.draft_overview(
                document.title, facts
            )
            _add_section("Project Overview", "project-overview", overview_blocks, SowSectionStatus.done, model=model_used)
            _add_section("Scope of Work", "scope-of-work", scope_blocks, SowSectionStatus.done, model=model_used)
        except IngestError as exc:
            logger.warning("SOW generation: overview drafting failed for version %s: %s", version_id, exc)
            fallback_blocks = lambda title: [{"type": "heading", "level": 2, "text": title}]  # noqa: E731
            _add_section(
                "Project Overview", "project-overview", fallback_blocks("Project Overview"),
                SowSectionStatus.error, error_message=str(exc),
            )
            _add_section(
                "Scope of Work", "scope-of-work", fallback_blocks("Scope of Work"),
                SowSectionStatus.error, error_message=str(exc),
            )

        # ── Pass 2b + Pass 3: draft each functional section, then audit it ─
        # Shared with patch_sow_task via sow_patch.draft_and_audit_section
        # (Phase 7) so a fix to this logic can't drift between the
        # full-generation and patch paths -- this loop's own behavior is
        # unchanged from before the Phase 7 refactor, just delegated.
        total = len(groups)
        for i, group in enumerate(groups, start=1):
            section_facts = [facts[idx] for idx in group["fact_indices"]]
            _update_job(
                stage=SowJobStage.drafting,
                progress=f"Drafting & auditing section {i}/{total}: {group['heading']}",
            )
            result = sow_patch.draft_and_audit_section(group["heading"], section_facts)
            _add_section(
                group["heading"], group["section_key"], result["content_blocks"], result["status"],
                error_message=result["error_message"], coverage_score=result["coverage_score"],
                coverage_gaps=result["coverage_gaps"],
            )
            models_used |= result["models_used"]

        # ── Pass 4: assembly (templated trailing sections, no LLM call) ─
        _update_job(stage=SowJobStage.assembly, progress="Assembling document")
        for spec in sow_assembly.build_templated_sections():
            _add_section(
                spec["heading"], spec["section_key"], spec["content_blocks"], SowSectionStatus.done
            )

        session.flush()

        done_count = sum(1 for s in sections_created if s.status == SowSectionStatus.done)
        total_count = len(sections_created)
        error_count = total_count - done_count

        if done_count == 0:
            version.status = SowVersionStatus.error
            version.error_message = "Every section failed to draft — see individual section errors."
            document.status = SowDocumentStatus.error
            job.status = SowJobStatus.error
        else:
            version.status = SowVersionStatus.done_with_errors if error_count else SowVersionStatus.done
            document.status = SowDocumentStatus.ready
            document.current_version_id = version.id
            job.status = SowJobStatus.done_with_errors if error_count else SowJobStatus.done

        version.generated_by_model = ", ".join(sorted(models_used)) if models_used else None
        job.completed_at = datetime.now(timezone.utc)
        job.stage_progress = f"{done_count}/{total_count} sections done"
        session.commit()
        logger.info(
            "SOW generation: version %s complete — %d/%d sections done (document %s -> %s)",
            version_id, done_count, total_count, document_id, document.status,
        )
    except Exception:
        logger.exception("SOW generation: unexpected failure for version %s", version_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(document_id, version_id)
        return
    finally:
        session.close()


@celery_app.task(
    name="sow_generation.patch_sow_task",
    bind=True,
    max_retries=0,
    soft_time_limit=1200,
)
def patch_sow_task(self, document_id: str, version_id: str, target_sections: list[str]) -> None:
    """Phase 7 — regenerate ONLY target_sections into a new patch version;
    every other section was already copied forward unchanged by the API
    endpoint (app/api/v1/sow.py::rewrite_document) before this task was
    even enqueued. See app/services/sow_patch.py's module docstring for
    the full scope (why framing/templated sections aren't targetable,
    why this doesn't re-run ledger extraction).

    target_sections here is already the FINAL list after the API layer's
    human-edit-protection filtering (plan §11.4) — every key in it is
    meant to be regenerated, no further filtering happens in this task.
    """
    from sqlalchemy import func

    from app.core.database import SessionLocal
    from app.models.sow import (
        SowDocument,
        SowDocumentStatus,
        SowDocumentVersion,
        SowGenerationJob,
        SowJobStage,
        SowJobStatus,
        SowRequirementsLedger,
        SowSection,
        SowSectionStatus,
        SowVersionStatus,
    )
    from app.services import sow_patch

    session = SessionLocal()
    try:
        document = session.get(SowDocument, document_id)
        version = session.get(SowDocumentVersion, version_id)
        if document is None or version is None:
            logger.error("SOW patch: document %s or version %s not found", document_id, version_id)
            return
        job = (
            session.query(SowGenerationJob)
            .filter(SowGenerationJob.version_id == version.id)
            .order_by(SowGenerationJob.created_at.desc())
            .first()
        )
        if job is None:
            logger.error("SOW patch: no job row for version %s", version_id)
            return

        def _update_job(*, stage=None, status=None, progress=None):
            if stage is not None:
                job.stage = stage
            if status is not None:
                job.status = status
            if progress is not None:
                job.stage_progress = progress
            session.commit()

        # Idempotency guard (same task_acks_late redelivery concern as
        # generate_sow_task) -- but here we must ONLY clear the sections
        # THIS task owns. Every other section in this version was already
        # copied forward by the API endpoint before enqueueing and must
        # never be touched by a redelivered retry of this task.
        (
            session.query(SowSection)
            .filter(SowSection.version_id == version.id, SowSection.section_key.in_(target_sections))
            .delete(synchronize_session=False)
        )
        session.commit()

        job.started_at = datetime.now(timezone.utc)
        _update_job(stage=SowJobStage.drafting, status=SowJobStatus.running, progress="Loading parent section headings")

        parent_sections_by_key = {}
        if version.parent_version_id is not None:
            parent_sections_by_key = {
                s.section_key: s
                for s in session.query(SowSection)
                .filter(SowSection.version_id == version.parent_version_id)
                .all()
            }

        # New sections continue the order_index sequence the copied-forward
        # sections already established, so the patch doesn't scramble
        # section order in the resulting version.
        max_order = (
            session.query(func.max(SowSection.order_index))
            .filter(SowSection.version_id == version.id)
            .scalar()
        )
        order_index = (max_order + 1) if max_order is not None else 0

        redrafted: list[SowSection] = []
        models_used: set[str] = set()
        total = len(target_sections)

        for i, key in enumerate(target_sections, start=1):
            parent_section = parent_sections_by_key.get(key)
            heading = parent_section.heading if parent_section else key
            _update_job(progress=f"Redrafting section {i}/{total}: {heading}")

            facts = (
                session.query(SowRequirementsLedger)
                .filter(
                    SowRequirementsLedger.document_id == document.id,
                    SowRequirementsLedger.assigned_section_key == key,
                    SowRequirementsLedger.superseded.is_(False),
                )
                .order_by(SowRequirementsLedger.created_at.asc())
                .all()
            )

            if not facts:
                sec = SowSection(
                    version_id=version.id,
                    order_index=order_index,
                    heading=heading,
                    section_key=key,
                    content_blocks=[{"type": "heading", "level": 2, "text": heading}],
                    status=SowSectionStatus.error,
                    error_message=(
                        "No requirements ledger facts are assigned to this section — "
                        "nothing to redraft from."
                    ),
                )
                session.add(sec)
                order_index += 1
                redrafted.append(sec)
                logger.warning(
                    "SOW patch: section '%s' (key=%s) has no assigned facts for version %s",
                    heading, key, version_id,
                )
                continue

            result = sow_patch.draft_and_audit_section(heading, facts)
            sec = SowSection(
                version_id=version.id,
                order_index=order_index,
                heading=heading,
                section_key=key,
                content_blocks=result["content_blocks"],
                status=result["status"],
                error_message=result["error_message"],
                coverage_score=result["coverage_score"],
                coverage_gaps=result["coverage_gaps"],
            )
            session.add(sec)
            order_index += 1
            redrafted.append(sec)
            models_used |= result["models_used"]

        _update_job(stage=SowJobStage.assembly, progress="Finalizing patch")
        session.flush()

        # Aggregate status over EVERY section in the new version (copied +
        # redrafted) -- mirrors generate_sow_task's own done/done_with_
        # errors/error computation, so a patch that broke one targeted
        # section isn't indistinguishable from one that broke everything.
        all_sections = session.query(SowSection).filter(SowSection.version_id == version.id).all()
        done_count = sum(1 for s in all_sections if s.status == SowSectionStatus.done)
        total_count = len(all_sections)
        error_count = total_count - done_count

        if done_count == 0:
            version.status = SowVersionStatus.error
            version.error_message = "Every section in this patch failed — see individual section errors."
            document.status = SowDocumentStatus.error
            job.status = SowJobStatus.error
        else:
            version.status = SowVersionStatus.done_with_errors if error_count else SowVersionStatus.done
            document.status = SowDocumentStatus.ready
            document.current_version_id = version.id
            job.status = SowJobStatus.done_with_errors if error_count else SowJobStatus.done

        # generated_by_model should reflect models across the WHOLE
        # version, not just the freshly-redrafted sections -- carry the
        # parent version's attribution forward and add anything new.
        parent_models: set[str] = set()
        if version.parent_version_id is not None:
            parent_version = session.get(SowDocumentVersion, version.parent_version_id)
            if parent_version and parent_version.generated_by_model:
                parent_models = {m.strip() for m in parent_version.generated_by_model.split(",") if m.strip()}
        version.generated_by_model = ", ".join(sorted(parent_models | models_used)) or None

        job.completed_at = datetime.now(timezone.utc)
        job.stage_progress = f"{len(redrafted)} section(s) redrafted, {done_count}/{total_count} total done"
        session.commit()
        logger.info(
            "SOW patch: version %s complete — %d section(s) redrafted, %d/%d total done "
            "(document %s -> %s)",
            version_id, len(redrafted), done_count, total_count, document_id, document.status,
        )
    except Exception:
        logger.exception("SOW patch: unexpected failure for version %s", version_id)
        session.rollback()
        session.close()
        _mark_unexpected_failure(document_id, version_id)
        return
    finally:
        session.close()
