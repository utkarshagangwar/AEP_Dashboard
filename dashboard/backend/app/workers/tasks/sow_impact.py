"""Celery task — work out which drafted sections a new source affects.

Runs after every successful ledger extraction, but only does anything once
the document has an actual generated version to compare against. Before
that there are no sections, and the user's next step is Generate anyway.

Strictly advisory: this stamps assigned_section_key on the new facts,
retires facts the new source restates, and records the affected section
keys on the document. It never redrafts. The UI reads
SowDocument.pending_section_keys to pre-tick those sections in the Rewrite
dialog; the user presses the button, and the existing patch_sow_task does
the work with no changes.

Failure is non-fatal by design — the extraction that triggered this has
already been committed and is valuable on its own. A failure here costs the
user a pre-ticked checkbox list, not their facts.
"""
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="sow_impact.analyze_source_impact_task", bind=True, max_retries=0)
def analyze_source_impact_task(self, source_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.sow import (
        SowDocument,
        SowDocumentStatus,
        SowRequirementsLedger,
        SowSection,
    )
    from app.services import sow_impact, sow_patch
    from app.services.design_ingest import IngestError

    session = SessionLocal()
    try:
        from app.models.sow import SowDocumentSource

        source = session.get(SowDocumentSource, source_id)
        if source is None:
            return
        document = session.get(SowDocument, source.document_id)
        if document is None or document.current_version_id is None:
            # Never generated — nothing to compare against, and Generate is
            # the user's next step regardless.
            return
        if document.status == SowDocumentStatus.generating:
            # A generation in flight will assign these facts itself as part
            # of its own grouping pass; doing it here too would race it.
            logger.info(
                "SOW impact: document %s is generating — skipping impact analysis "
                "for source %s", document.id, source_id,
            )
            return

        sections = (
            session.query(SowSection)
            .filter(SowSection.version_id == document.current_version_id)
            .order_by(SowSection.order_index)
            .all()
        )
        non_patchable = sow_patch.non_patchable_section_keys()
        sections_by_key = {
            s.section_key: s.heading
            for s in sections
            if s.section_key not in non_patchable
        }
        if not sections_by_key:
            return

        new_facts = (
            session.query(SowRequirementsLedger)
            .filter(
                SowRequirementsLedger.document_id == document.id,
                SowRequirementsLedger.source_artifact_id == source.artifact_id,
                SowRequirementsLedger.superseded.is_(False),
            )
            .order_by(SowRequirementsLedger.created_at.asc())
            .all()
        )
        if not new_facts:
            return

        # A source cannot be "new material" for a version that was built from
        # it. The import path builds version 1 verbatim from the very document
        # it just extracted facts from; if this source is the ONLY source with
        # live facts on the document, the current version can only have come
        # from it, so there is nothing pre-existing for it to affect. Without
        # this, a fresh import diffs against itself and every section comes
        # back "affected by new source". Checked here as well as at the call
        # site so a re-queue, a manual replay, or a future caller cannot
        # reintroduce the same self-comparison.
        other_source_facts = (
            session.query(SowRequirementsLedger.id)
            .filter(
                SowRequirementsLedger.document_id == document.id,
                SowRequirementsLedger.source_artifact_id != source.artifact_id,
                SowRequirementsLedger.superseded.is_(False),
            )
            .first()
        )
        if other_source_facts is None:
            logger.info(
                "SOW impact: source %s is the document's only source — the current "
                "version was built from it, so there is nothing to affect", source_id,
            )
            # Clear any stale flags a previous run of this bug may have left,
            # so an already-imported document heals on its next extraction.
            if document.pending_section_keys or document.pending_new_fact_count:
                document.pending_section_keys = None
                document.pending_new_fact_count = None
                session.commit()
            return

        # Retire what this source restates BEFORE assigning, so a superseded
        # older fact can't keep a section on the affected list on its own.
        sow_impact.mark_superseded(session, document.id, new_facts)

        try:
            assignments = sow_impact.assign_new_facts_to_sections(
                new_facts, sections_by_key
            )
        except IngestError as exc:
            logger.warning(
                "SOW impact: could not assign new facts for source %s: %s", source_id, exc
            )
            session.rollback()
            return

        affected: list[str] = []
        for index, key in assignments.items():
            new_facts[index].assigned_section_key = key
            if key not in affected:
                affected.append(key)

        unassigned = len(new_facts) - len(assignments)

        # Preserve document order: the Rewrite dialog lists sections in
        # order, so the pre-ticked set should read the same way.
        order = {s.section_key: s.order_index for s in sections}
        affected.sort(key=lambda k: order.get(k, 10**6))

        document.pending_section_keys = affected or None
        document.pending_new_fact_count = len(new_facts)
        session.commit()

        logger.info(
            "SOW impact: source %s -> %d new fact(s) affecting %d section(s) "
            "(%d fact(s) fit no existing section)",
            source_id, len(new_facts), len(affected), unassigned,
        )
    except Exception:
        logger.exception("SOW impact: unexpected failure for source %s", source_id)
        session.rollback()
    finally:
        session.close()
