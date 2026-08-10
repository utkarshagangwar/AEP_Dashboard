"""ORM models for SOW Creation & Rewrite (Phase 0 foundation).

See SOW_FEATURE_PLAN.md at the repo root for the full design. Short version:
this feature runs the opposite direction from the existing Visual QA "SOW
Checkpoints" pipeline (app/services/design_ingest.py, which turns an
uploaded SOW into checkpoints) -- here, meeting transcripts, meeting
recordings, and design artifacts are turned INTO a SOW document, which can
then optionally be fed into that same existing checkpoint extractor.

Tables:
  SowDocument            one row per SOW a user is authoring/rewriting.
  SowDocumentVersion      every full-generation or patch version of a
                          document -- versions are never mutated in place,
                          only ever superseded by a new version row.
  SowSection              structured content for one section of one
                          version (content_blocks JSONB -- see plan §11.6
                          for the block schema).
  SowRequirementsLedger   extracted facts / UI-element inventory a
                          version's sections are drafted from and, in
                          Pass 3, audited against for completeness.
  SowGenerationJob        Celery job progress/status for one version's
                          generation or patch run.

Additive only: no existing table or model is modified. design_artifacts
gains two new artifact_type values ('meeting_transcript',
'meeting_recording') via migration only -- no schema change to that model.
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base


class SowDocumentStatus(str, PyEnum):
    draft = "draft"            # created, no successful generation yet
    generating = "generating"  # a generate/rewrite job is in flight (concurrency
                                # guard -- plan §11.3; a second generate/rewrite
                                # request while in this state gets a 409)
    ready = "ready"            # current_version_id points at a usable version
    error = "error"            # most recent generation/patch attempt failed outright


class SowVersionKind(str, PyEnum):
    full_generation = "full_generation"
    patch = "patch"


class SowVersionStatus(str, PyEnum):
    pending = "pending"
    generating = "generating"
    done = "done"                        # every section done, full coverage pass ran
    done_with_errors = "done_with_errors"  # at least one section done, at least one
                                            # errored -- never silently equivalent to
                                            # "done" (plan §11.5)
    error = "error"                      # zero sections completed


class SowSectionStatus(str, PyEnum):
    pending = "pending"
    generating = "generating"
    done = "done"
    error = "error"


class SowJobStage(str, PyEnum):
    ledger_extraction = "ledger_extraction"
    drafting = "drafting"
    audit = "audit"
    assembly = "assembly"


class SowJobStatus(str, PyEnum):
    queued = "queued"
    running = "running"
    done = "done"
    done_with_errors = "done_with_errors"
    error = "error"


class SowLedgerFactType(str, PyEnum):
    feature = "feature"
    decision = "decision"
    ui_element = "ui_element"
    open_question = "open_question"


class SowSourceStatus(str, PyEnum):
    """Ledger-extraction status of one (document, artifact) attachment --
    see SowDocumentSource. Independent of DesignArtifact.parse_status,
    which belongs to the separate SOW Checkpoints/Video Walkthrough
    pipeline and must not be conflated with this one even when the same
    artifact row is reused across both."""

    pending = "pending"
    processing = "processing"
    done = "done"
    # Some parts of a chunked document extracted, some did not. Distinct
    # from `done` on purpose (migration 0040): partial results ARE saved
    # now, and the only thing making that safe is that a partial source can
    # never be mistaken for a complete one. error_message names the failing
    # parts so the user knows exactly what to retry.
    done_with_errors = "done_with_errors"
    error = "error"


# Progress stage tokens written to SowDocumentSource.progress_stage
# (migration 0039). Deliberately plain strings, not a DB Enum: they are
# display-only breadcrumbs, and adding a stage must never require a
# migration or risk an enum-value mismatch against an older worker still
# running mid-deploy. The frontend maps unknown values to a generic
# "Working" label, so an unrecognised token degrades, never breaks.
SOW_SOURCE_STAGE_READING = "reading"       # pulling text/blocks out of the file
SOW_SOURCE_STAGE_CHUNKING = "chunking"     # splitting on structure
SOW_SOURCE_STAGE_EXTRACTING = "extracting"  # LLM pass, per chunk
SOW_SOURCE_STAGE_SAVING = "saving"         # dedup + writing ledger rows

SOW_SOURCE_PROGRESS_STAGES = (
    SOW_SOURCE_STAGE_READING,
    SOW_SOURCE_STAGE_CHUNKING,
    SOW_SOURCE_STAGE_EXTRACTING,
    SOW_SOURCE_STAGE_SAVING,
)


class SowUIElementType(str, PyEnum):
    button = "button"
    dropdown = "dropdown"
    filter = "filter"
    checkbox = "checkbox"
    toggle = "toggle"
    slider = "slider"
    three_dot_menu = "three_dot_menu"
    tab = "tab"
    modal = "modal"
    other = "other"


class SowDocument(Base):
    __tablename__ = "sow_documents"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title = mapped_column(String(500), nullable=False)
    # URL identifier, derived from title (migration 0048). Globally unique
    # against SowDocumentSlugHistory.slug, not just against other documents'
    # CURRENT slugs -- see that table's docstring for why. Assigned on
    # create and rotated on rename (api/v1/sow.py's _assign_slug); every
    # value it has ever held stays resolvable via history, so an old
    # bookmark keeps working instead of 404ing after a rename.
    slug = mapped_column(String(120), nullable=False, index=True, unique=True)
    status = mapped_column(
        Enum(SowDocumentStatus, name="sow_document_status_enum", create_type=False),
        nullable=False,
        default=SowDocumentStatus.draft,
        server_default="draft",
        index=True,
    )
    # Soft delete -- matches Project.is_active (app/models/project.py). DELETE
    # hides a document rather than destroying AI-generated work product;
    # list/get endpoints filter to True by default.
    is_active = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    # Points at the version currently considered "the" SOW for this document.
    # Nullable FK (not enforced NOT NULL) since a freshly-created document has
    # no version yet. Set only once a version reaches done/done_with_errors.
    current_version_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Section keys a newly attached source affects, computed by
    # app.workers.tasks.sow_impact after each extraction (migration 0040).
    # ADVISORY ONLY -- the Rewrite dialog pre-ticks these; nothing redrafts
    # off them without the user pressing the button. Cleared when a
    # generation or patch consumes them.
    pending_section_keys = mapped_column(JSONB, nullable=True)
    # How many not-yet-drafted facts arrived with that source. Drives the
    # "new source affects N sections" banner copy.
    pending_new_fact_count = mapped_column(Integer, nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SowDocumentSlugHistory(Base):
    """Every slug a SowDocument has ever been assigned (migration 0048), one
    row per slug. `slug` is UNIQUE across this ENTIRE table, not scoped to a
    document -- that is the whole point of it. Two consequences:

      1. Old-link redirects: when a rename rotates a document onto a new
         slug, the previous row is left in place. Resolving a request finds
         it, follows document_id to the still-live document, and the caller
         (api/v1/sow.py's document lookup) can tell the slug in the URL no
         longer matches the document's current one -- that is what lets the
         frontend silently redirect an old bookmark to the live URL instead
         of 404ing it.

      2. Slug hijack prevention: because uniqueness is global and permanent,
         a *different* document can never be created or renamed onto a slug
         some other document used to own, even after that document renamed
         away from it or was soft-deleted. Without this, someone could title
         a new document to reclaim another document's old URL and silently
         receive whatever traffic/bookmarks were meant for the original.

    Never pruned. At this app's scale (one row per document per rename) an
    unbounded history is not a real storage concern, and pruning would
    reopen the hijack window above for whatever it deleted.
    """

    __tablename__ = "sow_document_slug_history"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug = mapped_column(String(120), nullable=False, unique=True, index=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SowDocumentVersion(Base):
    __tablename__ = "sow_document_versions"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = mapped_column(Integer, nullable=False)  # 1-based, per document
    kind = mapped_column(
        Enum(SowVersionKind, name="sow_version_kind_enum", create_type=False),
        nullable=False,
    )
    # Set only for kind=patch -- the version this one patched. Null for the
    # first full_generation of a document, and for any subsequent
    # full_generation (a full regen deliberately starts a fresh lineage
    # rather than claiming to "patch" everything).
    parent_version_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = mapped_column(
        Enum(SowVersionStatus, name="sow_version_status_enum", create_type=False),
        nullable=False,
        default=SowVersionStatus.pending,
        server_default="pending",
    )
    error_message = mapped_column(Text, nullable=True)
    # Model that drove drafting -- for auditability when free/router models
    # rotate, same rationale as DesignRule.parsed_by_model.
    generated_by_model = mapped_column(String(200), nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SowSection(Base):
    __tablename__ = "sow_sections"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index = mapped_column(Integer, nullable=False)
    heading = mapped_column(String(500), nullable=False)
    # Stable identity for this section across regenerations -- how the
    # editor's "regenerate this section" and the Rewrite modal's per-section
    # targeting keep pointing at the same logical section even after a
    # patch or full regen. See plan §11.4. Unique per version, not globally.
    section_key = mapped_column(String(200), nullable=False)
    # Typed block list -- see plan §11.6 for the concrete schema
    # (heading/paragraph/control_spec/bullet_list/table/callout). Validated
    # by a pydantic model on every write, LLM-produced or hand-edited.
    content_blocks = mapped_column(JSONB, nullable=False, default=list)
    schema_version = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # 0-100, from Pass 3's completeness audit: ledger rows assigned to this
    # section that have a matching control_spec block / covered by
    # something in the ledger. Null until the audit pass has run once.
    coverage_score = mapped_column(Integer, nullable=True)
    # Ledger rows the audit pass could not find represented in the draft --
    # these get auto-appended as flagged "Additional elements" blocks, never
    # silently dropped (plan §2 Pass 3 / §11.5).
    coverage_gaps = mapped_column(JSONB, nullable=True)
    status = mapped_column(
        Enum(SowSectionStatus, name="sow_section_status_enum", create_type=False),
        nullable=False,
        default=SowSectionStatus.pending,
        server_default="pending",
    )
    error_message = mapped_column(Text, nullable=True)
    # Once true, this section is skipped by future patch/regen runs unless
    # explicitly overridden -- mirrors AISkill's existing manual-edit
    # protection (see app/services/skill_store.py). Plan §11.4.
    edited_by_human = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    edited_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SowRequirementsLedger(Base):
    """One row per extracted fact -- feature, decision, open question, or (most
    important for the coverage guarantee) a single UI element/control. This
    is the checklist Pass 3 audits generated prose against; it is not itself
    prose. See plan §2 Pass 1.
    """

    __tablename__ = "sow_requirements_ledger"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fact_type = mapped_column(
        Enum(SowLedgerFactType, name="sow_ledger_fact_type_enum", create_type=False),
        nullable=False,
    )
    # Populated only for fact_type=ui_element rows.
    element_type = mapped_column(
        Enum(SowUIElementType, name="sow_ui_element_type_enum", create_type=False),
        nullable=True,
    )
    label = mapped_column(String(500), nullable=False)
    location = mapped_column(String(500), nullable=True)
    behavior_notes = mapped_column(Text, nullable=True)
    # e.g. "video_still#3", "transcript:00:14:32", "design_frame:checkout.png"
    # -- traceability back to exactly what produced this fact.
    source_ref = mapped_column(String(500), nullable=True)
    # The heading path this fact sat under in the SOURCE document, e.g.
    # ["InterviewGod Platform v2.5.2", "21. Demo List Page"] (migration
    # 0040). Written by the chunker from the document's real structure, never
    # by the model, so it cannot be hallucinated. Null for transcripts,
    # recordings and design images, which have no document outline.
    #
    # This is what makes "keep the imported document's format" a property of
    # stored data: sow_drafting.group_ledger_into_sections groups on it
    # directly when most facts carry it, reproducing the source's own section
    # order instead of asking a model to invent a new one.
    source_heading_path = mapped_column(JSONB, nullable=True)
    assigned_section_key = mapped_column(String(200), nullable=True)
    # Retired (not deleted) once a rewrite supersedes it with a fresher fact
    # from newer source material -- preserves the ledger's audit trail
    # instead of losing history on every patch.
    superseded = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SowGenerationJob(Base):
    __tablename__ = "sow_generation_jobs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = mapped_column(
        Enum(SowJobStage, name="sow_job_stage_enum", create_type=False),
        nullable=False,
        default=SowJobStage.ledger_extraction,
        server_default="ledger_extraction",
    )
    # Human-readable progress within the current stage, e.g. "7/12 sections".
    stage_progress = mapped_column(String(200), nullable=True)
    status = mapped_column(
        Enum(SowJobStatus, name="sow_job_status_enum", create_type=False),
        nullable=False,
        default=SowJobStatus.queued,
        server_default="queued",
    )
    error_message = mapped_column(Text, nullable=True)
    started_at = mapped_column(DateTime, nullable=True)
    completed_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    # Bumped on every stage/progress transition -- sow_reconcile.py's stale-
    # job sweep (plan §11.2) marks any job 'running' with updated_at older
    # than SOW_JOB_STALE_MINUTES as errored, so a dead worker never leaves a
    # job spinning in the UI forever.
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SowDocumentSource(Base):
    """One (document, artifact) attachment -- a meeting transcript, meeting
    recording, or design reference attached to a SOW document as input
    material, plus this document's own ledger-extraction status for it
    (Phase 1). Added in migration 0029, after Phase 0 shipped
    SowRequirementsLedger without anything representing "attached but not
    yet extracted" or letting a source be listed/detached.
    """

    __tablename__ = "sow_document_sources"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sow_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = mapped_column(
        Enum(SowSourceStatus, name="sow_source_status_enum", create_type=False),
        nullable=False,
        default=SowSourceStatus.pending,
        server_default="pending",
        index=True,
    )
    error_message = mapped_column(Text, nullable=True)
    # Live extraction progress (migration 0039). Display-only -- no code
    # branches on these, so a worker that never writes them (or dies before
    # it can) degrades to exactly the pre-0039 behaviour: a plain status
    # badge. progress_stage is one of the SOW_SOURCE_PROGRESS_STAGES tokens
    # below; progress_total <= 1 or NULL means the work is a single
    # indivisible LLM call and no honest percentage exists for it.
    progress_stage = mapped_column(String(40), nullable=True)
    progress_current = mapped_column(Integer, nullable=True)
    progress_total = mapped_column(Integer, nullable=True)
    # Informational only -- see migration 0029's comment.
    ledger_fact_count = mapped_column(Integer, nullable=True)
    # The imported document's own table of contents, in its original order
    # (migration 0040): [{"heading_path": [...], "heading": str, "locator":
    # str|null}]. Only existing-SOW sources populate it. Kept so a later
    # regeneration can reproduce the source's structure rather than an
    # invented one -- see SowRequirementsLedger.source_heading_path, which is
    # the per-fact counterpart this is derived alongside.
    source_outline = mapped_column(JSONB, nullable=True)
    added_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    # Bumped on every status transition -- sow_reconcile.py's stale-source
    # sweep (mirrors visual_qa_reconcile.py) keys off this.
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
