"""Pydantic schemas for the SOW Creation & Rewrite API.

Phase 0: document CRUD. Phase 1: source attachment (transcript/recording/
design) and the raw requirements-ledger dump. Phase 3 (this addition):
generation jobs, versions, and sections (drafting + assembly — no
completeness-audit fields populated yet, that's Phase 4). Export and
rewrite/patch schemas land in later phases per SOW_FEATURE_PLAN.md.
"""
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SowDocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    project_id: Optional[UUID] = None


class SowDocumentUpdate(BaseModel):
    # Phase 0 only supports renaming. Status/current_version_id are written
    # exclusively by the generation pipeline (Phase 2+), never by direct
    # client PATCH -- exposing them here would let a client fake a document
    # into "ready" with no version behind it.
    title: str = Field(..., min_length=1, max_length=500)


class SowDocumentOut(BaseModel):
    id: UUID
    project_id: Optional[UUID]
    title: str
    status: str
    is_active: bool
    current_version_id: Optional[UUID]
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SowDocumentSourceOut(BaseModel):
    id: UUID
    document_id: UUID
    artifact_id: Optional[UUID]
    artifact_type: Optional[str] = None   # denormalized from design_artifacts for display
    file_name: Optional[str] = None       # denormalized from design_artifacts for display
    status: str
    error_message: Optional[str]
    ledger_fact_count: Optional[int]
    added_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SowRequirementsLedgerOut(BaseModel):
    id: UUID
    document_id: UUID
    source_artifact_id: Optional[UUID]
    fact_type: str
    element_type: Optional[str]
    label: str
    location: Optional[str]
    behavior_notes: Optional[str]
    source_ref: Optional[str]
    superseded: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SowGenerationJobOut(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    stage: str
    stage_progress: Optional[str]
    status: str
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class SowSectionOut(BaseModel):
    id: UUID
    order_index: int
    heading: str
    section_key: str
    status: str
    error_message: Optional[str]
    content_blocks: list[dict[str, Any]]
    rendered_markdown: str  # rendered on demand from content_blocks, never stored (plan §11.7)
    coverage_score: Optional[int] = None    # Phase 4: 0-100, null for framing/templated
                                             # sections that are never audited (see
                                             # app/services/sow_audit.py's module docstring)
    coverage_gaps: Optional[list[dict[str, Any]]] = None  # Phase 4: facts the audit
                                                            # could not find represented
    edited_by_human: bool = False


class SowVersionOut(BaseModel):
    id: UUID
    document_id: UUID
    version_number: int
    kind: str
    parent_version_id: Optional[UUID] = None  # set for kind=patch versions (Phase 7)
    status: str
    error_message: Optional[str]
    generated_by_model: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SowVersionDetailOut(SowVersionOut):
    sections: list[SowSectionOut]


class SowSectionPatch(BaseModel):
    """Phase 5: manual hand-edit of one section's structured content.
    Server-side re-validates every block through the same schema LLM
    output goes through (app/api/v1/sow.py::patch_section) -- this schema
    only enforces "a non-empty list of dicts," not the full block-type
    contract, since Pydantic can't easily express the tagged-union shape
    content_blocks uses without a lot of ceremony for a field that's about
    to be re-validated in detail anyway.
    """
    content_blocks: list[dict[str, Any]] = Field(..., min_length=1)


class SowExportRequest(BaseModel):
    """Phase 6. format is validated against a fixed set here (cheap,
    immediate 422 on garbage input) -- app/api/v1/sow.py::export_document
    still branches on the exact string rather than trusting this alone."""
    format: str = Field(..., pattern="^(md|docx|pdf)$")


class SowSendToCheckpointsOut(BaseModel):
    """Phase 6. Wraps the current version as a `sow`-type DesignArtifact
    and hands it to the existing (unmodified) design_ingest checkpoint
    pipeline -- see app/api/v1/sow.py::send_to_checkpoints."""
    artifact_id: UUID
    reused: bool  # true if an identical export (by sha256) was already ingested
    message: str


class SowRewriteRequest(BaseModel):
    """Phase 7: the patch path. Full regeneration is unchanged POST
    .../generate -- this is specifically "regenerate only these sections."
    """
    target_sections: list[str] = Field(..., min_length=1)
    # Section keys from target_sections that should be force-regenerated
    # even though they're edited_by_human=true (plan §11.4). Any
    # target_sections key NOT in here that IS hand-edited gets silently
    # copied through unchanged instead of regenerated.
    override_manual_edits: list[str] = Field(default_factory=list)
