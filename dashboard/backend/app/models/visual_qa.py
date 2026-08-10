"""ORM models for Visual QA (Memory Bank + audit runs) — Phase 1.

Tables:
  design_artifacts — uploaded design sources (Figma PNG, SOW, video), deduped
                     by sha256 so heavy files are ingested once ("Memory Bank").
  sow_parts        — chunks a large SOW is split into; each analyzed
                     independently, merged into the artifact's design_rules row.
  design_rules     — parsed visual checkpoints produced by The Brain, one row
                     per artifact (JSONB payload).
  visual_runs      — one Visual Audit execution (The Judge) per row.
  visual_findings  — individual discrepancies found in a run, tagged by which
                     engine found them (pixel-diff = deterministic, vision = AI).

Additive only: no existing table or model is modified.
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base


class ArtifactType(str, PyEnum):
    figma_png = "figma_png"      # exported Figma frame (image upload or Figma API)
    sow = "sow"                  # SOW / requirements document
    video = "video"              # design walkthrough video
    # Added for SOW Creation & Rewrite (see app/models/sow.py) -- meeting
    # inputs feeding SOW generation, reusing this table's existing sha256
    # Memory Bank dedupe rather than a parallel storage mechanism.
    meeting_transcript = "meeting_transcript"  # pasted/uploaded text transcript
    meeting_recording = "meeting_recording"    # raw audio/video recording
    # Import SOW (SOW tab): an uploaded pre-existing SOW/requirements
    # document (.docx/.pdf/.txt/.md) used as a source to seed a
    # sow_documents ledger baseline via app/services/sow_import.py +
    # app/services/sow_ledger.py's extract_ledger_from_sow_document*.
    # Deliberately distinct from `sow` (above), which belongs to the
    # separate, unmodified SOW-Checkpoints/Vibe-Testing extraction pipeline
    # (app/services/design_ingest.py) -- keeping the two apart avoids either
    # pipeline accidentally picking up the other's artifacts.
    sow_import = "sow_import"


class ParseStatus(str, PyEnum):
    not_required = "not_required"  # e.g. figma_png references — no parsing step
    pending = "pending"            # queued for The Brain
    processing = "processing"
    done = "done"                  # design_rules row exists
    error = "error"                # see parse_error


class VisualRunStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    passed = "passed"            # no findings above threshold
    failed = "failed"            # discrepancies found
    partial = "partial"          # pixel-diff completed but vision pass unavailable
    error = "error"
    cancelled = "cancelled"


class FindingEngine(str, PyEnum):
    pixel_diff = "pixel_diff"    # deterministic — authoritative for color/spacing
    vision = "vision"            # AI — authoritative for structure/missing elements


class FindingSeverity(str, PyEnum):
    critical = "critical"
    major = "major"
    minor = "minor"
    info = "info"


class DesignArtifact(Base):
    __tablename__ = "design_artifacts"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    artifact_type = mapped_column(
        Enum(ArtifactType, name="artifact_type_enum", create_type=False),
        nullable=False,
    )
    file_name = mapped_column(String(500), nullable=False)
    sha256 = mapped_column(String(64), nullable=False, index=True)  # dedupe key
    storage_path = mapped_column(Text, nullable=False)
    # Which live page/URL this artifact represents (e.g. "/checkout") so the
    # Judge knows what to compare it against. Nullable for SOW/video.
    target_page = mapped_column(String(1000), nullable=True)
    # User-declared product/platform name this video is a walkthrough of —
    # mandatory at the API layer for video uploads (never inferred/assumed
    # by the model). Null for sow/figma_png rows.
    platform_name = mapped_column(String(300), nullable=True)
    # SOW/video ingestion lifecycle (Phase 3+). figma_png rows stay 'not_required'.
    parse_status = mapped_column(
        Enum(ParseStatus, name="parse_status_enum", create_type=False),
        nullable=False,
        default=ParseStatus.not_required,
        server_default="not_required",
    )
    parse_error = mapped_column(Text, nullable=True)
    # Number of chunks a large SOW was split into (see SowPart). Always 1 for
    # figma_png/video artifacts and for SOWs small enough to need no chunking.
    total_parts = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    # Bumped on every write (parse_status transitions in particular) — used
    # by visual_qa_reconcile to detect a row stuck 'processing' because the
    # worker analyzing it died mid-flight.
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SowPart(Base):
    """One chunk of a large SOW document, analyzed independently (Phase 3 chunking).

    A SOW that fits in a single part still gets exactly one SowPart row, kept
    in lock-step with DesignArtifact.total_parts. Checkpoints from every
    'done' part are merged (concatenated by part_number) into the artifact's
    single DesignRule row.
    """

    __tablename__ = "sow_parts"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_number = mapped_column(Integer, nullable=False)  # 1-based
    content = mapped_column(Text, nullable=False)
    char_count = mapped_column(Integer, nullable=False)
    status = mapped_column(
        Enum(ParseStatus, name="parse_status_enum", create_type=False),
        nullable=False,
        default=ParseStatus.pending,
        server_default="pending",
    )
    error = mapped_column(Text, nullable=True)
    checkpoints = mapped_column(JSONB, nullable=True)
    parsed_by_model = mapped_column(String(200), nullable=True)

    # ── Chunk provenance (SOW_CHUNKING_PLAN Phase 3, migration 0038) ──
    # All nullable with no backfill: parts are created once at ingest and
    # never re-chunked, so rows written by the old character-window
    # splitter simply carry NULLs and are left alone. Re-ingesting an
    # artifact is the migration path.
    #
    # heading_path: JSON array, the section breadcrumb this part sits in,
    #   e.g. ["2. Functional Requirements", "2.1 Candidate List"]. This is
    #   what replaced the bare "part 3 of 7" the LLM used to receive.
    heading_path = mapped_column(JSONB, nullable=True)
    # locator: "p.12" / "§4.3.2" / "00:14:32" -- traceability back to the
    #   exact place in the source this part came from.
    locator = mapped_column(String(200), nullable=True)
    # strategy: which chunking strategy produced this part. The value
    #   "hard_split" is the DEGRADATION SIGNAL -- it means the part had to
    #   be cut at an arbitrary point because a single unit exceeded the
    #   budget. Surfaced in the UI (plan §5) so it is assertable from a
    #   vibe test rather than visible only in worker logs.
    strategy = mapped_column(String(40), nullable=True)
    # context_header: the exact framing block sent to the LLM alongside
    #   this part's content. Stored for reproducibility -- without it, a
    #   bad extraction cannot be diagnosed after the fact.
    context_header = mapped_column(Text, nullable=True)

    # ── Testability gate audit trail (migration 0043) ──
    #
    # excluded_zones: what app.services.tdd_extraction's Stage 0 decided was
    #   NOT product behaviour and therefore never sent for extraction —
    #   [{heading, zone_kind, reason, char_count, classifier}]. This column
    #   is the reason the gate is safe to have at all: the alternative to
    #   recording exclusions is a filter nobody can audit, and "the extractor
    #   quietly decided your requirements section was a glossary" is exactly
    #   the failure mode that would be impossible to notice otherwise.
    excluded_zones = mapped_column(JSONB, nullable=True)
    # coverage_json: tdd_extraction.scorecard() for this part — counts by
    #   test type / category / grounding, the negative_edge_ratio, and any
    #   behaviour that came back missing a variant its category requires.
    #   Makes a regression in extraction QUALITY (as opposed to extraction
    #   failure) visible without re-reading every generated skill.
    coverage_json = mapped_column(JSONB, nullable=True)

    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DesignRule(Base):
    __tablename__ = "design_rules"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Parsed checkpoints: list of {element, property, expected, source} dicts.
    checkpoints = mapped_column(JSONB, nullable=False)
    # Model that produced the parse — for auditability when free models rotate.
    parsed_by_model = mapped_column(String(200), nullable=True)
    parsed_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class VisualRun(Base):
    __tablename__ = "visual_runs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    environment = mapped_column(String(200), nullable=True)
    target_url = mapped_column(Text, nullable=False)
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = mapped_column(
        Enum(VisualRunStatus, name="visual_run_status_enum", create_type=False),
        nullable=False,
        default=VisualRunStatus.pending,
    )
    screenshot_path = mapped_column(Text, nullable=True)   # captured live page
    diff_image_path = mapped_column(Text, nullable=True)   # pixel-diff overlay
    pixel_mismatch_pct = mapped_column(Integer, nullable=True)  # 0–100, rounded
    # Free-text requirement/checkpoint reference (New Vibe Test Phase 1 —
    # UI Test flow). Same column name/shape as AITestRun.linked_requirement.
    linked_requirement = mapped_column(String(500), nullable=True)
    summary = mapped_column(Text, nullable=True)
    error_message = mapped_column(Text, nullable=True)
    started_at = mapped_column(DateTime, nullable=True)
    completed_at = mapped_column(DateTime, nullable=True)
    duration_ms = mapped_column(Integer, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class VisualFinding(Base):
    __tablename__ = "visual_findings"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visual_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    engine = mapped_column(
        Enum(FindingEngine, name="finding_engine_enum", create_type=False),
        nullable=False,
    )
    severity = mapped_column(
        Enum(FindingSeverity, name="finding_severity_enum", create_type=False),
        nullable=False,
        default=FindingSeverity.minor,
    )
    element = mapped_column(String(500), nullable=True)    # what was checked
    issue = mapped_column(Text, nullable=False)            # human-readable finding
    expected = mapped_column(Text, nullable=True)          # e.g. "#1A73E8"
    actual = mapped_column(Text, nullable=True)            # e.g. "#1B74E9"
    # Bounding box of the region, percentages of viewport: {x,y,w,h}
    region = mapped_column(JSONB, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ProjectUiInventory(Base):
    """What a project's UI is actually CALLED — one row per project.

    THE PROBLEM IT SOLVES. Extraction only ever saw text, so a checkpoint said
    "click Submit Application" because that is what the requirements document
    called the button, while the product's button says "Apply Now". The test
    then fails for a reason that is neither a product defect nor a spec gap,
    which is the most demoralising kind of red result there is.

    This row is the vocabulary that closes that gap: screens, buttons, fields
    and nav items read off the project's uploaded evidence (design_artifacts
    of type figma_png, plus labels already recovered from digested
    walkthrough videos). Built ONCE per project and reused by every SOW
    imported for it, which is what keeps it far cheaper than having an agent
    navigate the live product per test — and needs no credentials and no
    deployed environment.

    IT IS VOCABULARY, NOT REQUIREMENTS. The extractor is told to use these
    names when the document describes the same control differently. It is
    never allowed to treat a label as evidence that a behaviour exists — a
    button visible in a screenshot is not a requirement, and letting the
    inventory add behaviours would reintroduce exactly the "everything becomes
    a TDD" defect from the other direction.

    Derived and disposable: every field can be rebuilt from the artifacts, so
    a stale or bad row is fixed by rebuilding rather than by hand-editing.
    """

    __tablename__ = "project_ui_inventory"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique: one inventory per project. Rebuilds update in place so the
    # extraction path never has to choose between two versions.
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Structured form: [{screen, controls[], fields[], nav[], messages[]}].
    # Kept alongside the rendered text so a future consumer (a locator
    # healer, a coverage view) can use the structure without re-parsing prose.
    inventory_json = mapped_column(JSONB, nullable=True)
    # Exactly the text handed to the extraction prompt. Stored rather than
    # re-rendered per call so that what the model actually saw is auditable
    # after the fact — the same reason sow_parts keeps context_header.
    rendered_text = mapped_column(Text, nullable=True)
    # Artifact ids this was built from. This is the staleness key: when the
    # project gains new evidence the set no longer matches and the inventory
    # is rebuilt automatically, which is the whole answer to "we added
    # screenshots after importing the first SOW".
    source_artifact_ids = mapped_column(JSONB, nullable=True)
    # Count of screens the vision pass actually described, so "built from 12
    # screenshots" can be told apart from "built from 12 screenshots and
    # understood 2 of them".
    screen_count = mapped_column(Integer, nullable=False, default=0)
    built_by_model = mapped_column(String(200), nullable=True)
    # Why the last build produced nothing usable, if it didn't. Recorded
    # rather than silently leaving rendered_text NULL, since "no evidence
    # uploaded" and "the vision call failed" need different responses.
    build_error = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SowIngestEvent(Base):
    """A step that actually happened during one artifact's extraction.

    WHY A TABLE AND NOT A DERIVED STATUS. The alternative was a fixed list of
    phases in the UI, ticked off by inspecting SowPart rows afterwards. That
    can only ever show the same four steps in the same order regardless of
    what the pipeline really did — it would claim "identifying feature
    sections" on a run with zoning disabled, and stay silent on the repair
    pass, the variant cap and the cross-part merge, which are the stages a
    reader most needs to know fired. PRODUCT.md's first design principle is
    that copy must never claim progress that isn't happening; a row written
    by the code that did the work is the only version that can't drift from
    it.

    Rows are append-only and describe history, so they deliberately survive a
    rolled-back ingest: "extraction started and then failed" is exactly what
    the reader needs, and deleting the evidence on failure would leave the
    panel blank at the one moment it matters most.

    `sequence` is per artifact and assigned by the emitter. SOW ingest is
    single-flight per document — never two parts of one artifact in flight
    (see sow_ingest._chain_next_part) — so there is no writer race to guard.
    """

    __tablename__ = "sow_ingest_events"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Null for document-level steps (chunking, the cross-part merge) — the UI
    # renders those unprefixed rather than pretending they belong to a part.
    part_number = mapped_column(Integer, nullable=True)
    sequence = mapped_column(Integer, nullable=False, default=0)
    # Machine-readable stage key (e.g. "zoning", "repair", "naming_reference").
    # Kept alongside the human description so the panel can pick an icon
    # without pattern-matching on prose.
    stage = mapped_column(String(40), nullable=False)
    # running | done | skipped | error.
    #   skipped is its own state on purpose: "the repair pass found nothing to
    #   repair" and "the repair pass never ran" are different facts, and
    #   collapsing them into done would misreport a disabled flag as work.
    status = mapped_column(String(20), nullable=False, default="running")
    description = mapped_column(Text, nullable=False)
    # Counts behind the sentence — {"segments": 14, "excluded": 6} — so the
    # numbers can be re-rendered or charted without re-parsing the prose.
    detail = mapped_column(JSONB, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
