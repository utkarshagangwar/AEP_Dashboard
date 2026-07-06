"""ORM models for Visual QA (Memory Bank + audit runs) — Phase 1.

Tables:
  design_artifacts — uploaded design sources (Figma PNG, SOW, video), deduped
                     by sha256 so heavy files are ingested once ("Memory Bank").
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
    # SOW/video ingestion lifecycle (Phase 3+). figma_png rows stay 'not_required'.
    parse_status = mapped_column(
        Enum(ParseStatus, name="parse_status_enum", create_type=False),
        nullable=False,
        default=ParseStatus.not_required,
        server_default="not_required",
    )
    parse_error = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)


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
