"""SOW Creation & Rewrite API.

See SOW_FEATURE_PLAN.md at the repo root for the full design and phased
delivery plan.

Phase 0: document lifecycle (create/list/get/rename/soft-delete).
Phase 1: attaching source material to a document — meeting transcript
(paste or .txt/.md upload), meeting recording (audio/video upload, Gemini
Files API digest), design reference (attach an existing figma_png artifact
or upload a new PNG) — and the raw requirements-ledger dump each source's
extraction produces. Phase 3 (this addition): full-document generation
(POST .../generate), job status polling, and reading back
versions/sections. No completeness audit yet (Phase 4) and no rewrite/
patch yet (Phase 7) — every "Generate" click creates a fresh
full_generation version; export and rewrite endpoints are deliberately NOT
stubbed out here (a stub that always 501s is worse than no route at all:
it shows up in the OpenAPI schema looking finished).

Feature-flagged behind SOW_ENABLED (default: off), same convention as
VISUAL_AUDIT_ENABLED in app/api/v1/visual_audit.py — every endpoint 404s
until explicitly enabled, so existing deployments see zero behavior change.

Access control: gated by the "sow" permission (app/core/permissions.py),
kept distinct from "vibe_testing" on purpose (plan §11.1). project_id is a
convenience filter only, not an access boundary — this codebase has no
per-project membership/ACL concept anywhere (verified against
app/api/v1/projects.py before writing this — see plan §11.9), so SOW access
is consistent with every other project-scoped resource (design_artifacts,
visual_runs, defects): anyone holding the permission can see any document.
"""
import hashlib
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.models.project import Project
from app.models.sow import (
    SowDocument,
    SowDocumentSource,
    SowDocumentStatus,
    SowDocumentVersion,
    SowGenerationJob,
    SowJobStage,
    SowJobStatus,
    SowRequirementsLedger,
    SowSection,
    SowSectionStatus,
    SowSourceStatus,
    SowVersionKind,
    SowVersionStatus,
)
from app.models.user import User
from app.models.visual_qa import ArtifactType, DesignArtifact, ParseStatus
from app.schemas.sow import (
    SowDocumentCreate,
    SowDocumentOut,
    SowDocumentSourceOut,
    SowDocumentUpdate,
    SowExportRequest,
    SowGenerationJobOut,
    SowRequirementsLedgerOut,
    SowRewriteRequest,
    SowSectionOut,
    SowSectionPatch,
    SowSendToCheckpointsOut,
    SowVersionDetailOut,
    SowVersionOut,
)
from app.services.audit_service import write_audit_log
from app.services.design_ingest import IngestError

logger = get_logger(__name__)

router = APIRouter(prefix="/sow", tags=["sow"])

# ── Upload validation constants (Phase 1) ────────────────────────────────────
_TRANSCRIPT_EXTENSIONS = (".txt", ".md")
_MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_DESIGN_IMAGE_BYTES = 10 * 1024 * 1024
_VALID_LEDGER_FACT_TYPES = ("feature", "decision", "ui_element", "open_question")


def _feature_enabled() -> None:
    """Gate every endpoint behind SOW_ENABLED (default: off), matching the
    VISUAL_AUDIT_ENABLED precedent."""
    if os.environ.get("SOW_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="SOW authoring is not enabled")


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _data_dir() -> str:
    """Shared visual_qa_data volume root -- same one design_artifacts/sow/
    video storage already uses (app.workers.tasks.visual_audit.data_dir),
    so the API and Celery worker containers see the same files without
    introducing a second storage location."""
    from app.workers.tasks.visual_audit import data_dir

    return data_dir()


def _max_recording_bytes() -> int:
    return int(os.environ.get("SOW_MAX_RECORDING_MB", "300")) * 1024 * 1024


def _max_recording_minutes() -> int:
    return int(os.environ.get("SOW_MAX_RECORDING_MINUTES", "60"))


def _generate_rate_limit() -> str:
    """Plan §11.8: POST .../generate and POST .../rewrite are the only
    endpoint class in this feature that can trigger multi-minute,
    multi-dollar LLM spend per call -- every other endpoint (CRUD,
    section edit, export) is cheap and unlimited. Passed as a callable
    (not a plain string) to @limiter.limit() so it's re-read per request,
    matching this file's existing env-var convention
    (_max_recording_bytes/_max_recording_minutes) of live reconfigurability
    without a restart, rather than baking the value in at import time.

    Self-review note: this was supposed to exist since Phase 3 (it's in
    the original plan) but was missed when /generate first shipped --
    added retroactively here alongside /rewrite rather than left missing.
    """
    return os.environ.get("SOW_GENERATE_RATE_LIMIT", "10/hour")


def _document_out(d: SowDocument) -> SowDocumentOut:
    return SowDocumentOut(
        id=d.id,
        project_id=d.project_id,
        title=d.title,
        status=d.status.value if hasattr(d.status, "value") else str(d.status),
        is_active=d.is_active,
        current_version_id=d.current_version_id,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _get_active_document_or_404(db: Session, document_id: uuid.UUID) -> SowDocument:
    doc = db.get(SowDocument, document_id)
    if doc is None or not doc.is_active:
        raise HTTPException(status_code=404, detail="SOW document not found")
    return doc


def _source_out(db: Session, source: SowDocumentSource) -> SowDocumentSourceOut:
    artifact = db.get(DesignArtifact, source.artifact_id) if source.artifact_id else None
    return SowDocumentSourceOut(
        id=source.id,
        document_id=source.document_id,
        artifact_id=source.artifact_id,
        artifact_type=(
            artifact.artifact_type.value
            if artifact and hasattr(artifact.artifact_type, "value")
            else (artifact.artifact_type if artifact else None)
        ),
        file_name=artifact.file_name if artifact else None,
        status=source.status.value if hasattr(source.status, "value") else str(source.status),
        error_message=source.error_message,
        ledger_fact_count=source.ledger_fact_count,
        added_by=source.added_by,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _attach_source(
    db: Session, doc: SowDocument, artifact: DesignArtifact, current_user: User
) -> SowDocumentSource:
    """Create the (document, artifact) attachment, or reset an existing one
    back to 'pending' if this exact pair is already attached — re-POSTing
    the same source is treated as an explicit retry (e.g. after fixing
    whatever caused a prior extraction to error), never a silent no-op,
    and never a duplicate row (unique index on document_id+artifact_id)."""
    existing = (
        db.query(SowDocumentSource)
        .filter(
            SowDocumentSource.document_id == doc.id,
            SowDocumentSource.artifact_id == artifact.id,
        )
        .first()
    )
    if existing:
        existing.status = SowSourceStatus.pending
        existing.error_message = None
        db.commit()
        db.refresh(existing)
        return existing

    source = SowDocumentSource(
        document_id=doc.id, artifact_id=artifact.id, added_by=current_user.id
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.post("/documents", response_model=SowDocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: SowDocumentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Create a new (empty, draft) SOW document. No source material or
    generation happens here — this only reserves the document shell that
    Phase 1+ endpoints (source upload, generate, rewrite) act on."""
    _feature_enabled()

    if payload.project_id is not None:
        project = db.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

    try:
        doc = SowDocument(
            project_id=payload.project_id,
            title=payload.title.strip(),
            created_by=current_user.id,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to create SOW document for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Could not create SOW document")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="create_sow_document",
        resource_type="sow_document",
        resource_id=str(doc.id),
        details={"title": doc.title, "project_id": str(doc.project_id) if doc.project_id else None},
        ip_address=_client_ip(request),
    )
    logger.info("SOW document %s created by %s", doc.id, current_user.id)
    return _document_out(doc)


@router.get("/documents", response_model=list[SowDocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List active SOW documents, optionally filtered by project (a
    convenience filter, not an access boundary — see module docstring).
    Read access requires only login, matching how design_artifacts/visual
    QA listing already behaves; write actions require the "sow" permission.
    """
    _feature_enabled()

    query = db.query(SowDocument).filter(SowDocument.is_active.is_(True))
    if project_id is not None:
        query = query.filter(SowDocument.project_id == project_id)
    docs = query.order_by(SowDocument.updated_at.desc()).limit(limit).all()
    return [_document_out(d) for d in docs]


@router.get("/documents/{document_id}", response_model=SowDocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    return _document_out(doc)


@router.patch("/documents/{document_id}", response_model=SowDocumentOut)
def rename_document(
    document_id: uuid.UUID,
    payload: SowDocumentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Rename a document. Status/current_version_id are never client-writable
    here — they are owned exclusively by the generation pipeline (Phase 2+)."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    old_title = doc.title
    try:
        doc.title = payload.title.strip()
        db.commit()
        db.refresh(doc)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to rename SOW document %s", document_id)
        raise HTTPException(status_code=500, detail="Could not rename SOW document")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="rename_sow_document",
        resource_type="sow_document",
        resource_id=str(doc.id),
        details={"old_title": old_title, "new_title": doc.title},
        ip_address=_client_ip(request),
    )
    return _document_out(doc)


@router.delete("/documents/{document_id}", status_code=status.HTTP_200_OK)
def delete_document(
    document_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Soft delete: sets is_active=False. Versions/sections/ledger rows are
    left in place (not cascaded) so this is always recoverable by an admin
    directly in the DB if a delete turns out to have been a mistake — a
    hard-delete admin path can be added later if actually needed."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    try:
        doc.is_active = False
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to delete SOW document %s", document_id)
        raise HTTPException(status_code=500, detail="Could not delete SOW document")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="delete_sow_document",
        resource_type="sow_document",
        resource_id=str(doc.id),
        details={"title": doc.title},
        ip_address=_client_ip(request),
    )
    logger.info("SOW document %s soft-deleted by %s", doc.id, current_user.id)
    # 200 + JSON body, not 204 -- the shared frontend apiDelete() client
    # (utils/apiClient.js) unconditionally calls res.json() on success,
    # matching every other DELETE endpoint in this codebase (see
    # projects.py::delete_project). A 204 here would parse-error on every
    # successful delete in the UI.
    return {"message": "SOW document deleted successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — source attachment (transcript / recording / design) + ledger dump
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/sources/transcript",
    response_model=SowDocumentSourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_transcript_source(
    document_id: uuid.UUID,
    request: Request,
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Attach a meeting transcript to this document — either an uploaded
    .txt/.md file or pasted text (exactly one). Enqueues ledger extraction;
    the extracted facts land in GET .../ledger once the source's status
    reaches 'done'."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    has_file = file is not None
    has_text = bool(text and text.strip())
    if has_file == has_text:  # both or neither
        raise HTTPException(
            status_code=400,
            detail="Provide either a transcript file or pasted text, not both/neither.",
        )

    if has_file:
        file_name = (file.filename or "transcript.txt")[:500]
        ext = os.path.splitext(file_name.lower())[1]
        if ext not in _TRANSCRIPT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Transcript must be a .txt or .md file")
        content = await file.read()
    else:
        file_name = "pasted-transcript.txt"
        content = text.strip().encode("utf-8")

    if len(content) > _MAX_TRANSCRIPT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Transcript exceeds the {_MAX_TRANSCRIPT_BYTES // (1024 * 1024)}MB limit.",
        )
    if not content.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty")

    sha = hashlib.sha256(content).hexdigest()
    artifact = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.sha256 == sha,
            DesignArtifact.project_id == doc.project_id,
            DesignArtifact.artifact_type == ArtifactType.meeting_transcript,
        )
        .first()
    )
    if artifact is None:
        transcript_dir = os.path.join(_data_dir(), "sow_meeting_transcript")
        os.makedirs(transcript_dir, exist_ok=True)
        storage_path = os.path.join(transcript_dir, f"{sha}.txt")
        with open(storage_path, "wb") as fh:
            fh.write(content)

        artifact = DesignArtifact(
            project_id=doc.project_id,
            artifact_type=ArtifactType.meeting_transcript,
            file_name=file_name,
            sha256=sha,
            storage_path=storage_path,
            # not_required: this artifact is parsed by sow_ledger (Phase 1
            # ledger extraction, tracked on SowDocumentSource.status), not
            # by the SOW-Checkpoints pipeline that ParseStatus otherwise
            # belongs to on this table.
            parse_status=ParseStatus.not_required,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

    source = _attach_source(db, doc, artifact, current_user)

    from app.workers.tasks.sow_ledger import extract_transcript_ledger_task

    extract_transcript_ledger_task.delay(str(source.id))
    logger.info(
        "SOW document %s: transcript source %s attached by %s", doc.id, source.id, current_user.id
    )
    return _source_out(db, source)


@router.post(
    "/documents/{document_id}/sources/recording",
    response_model=SowDocumentSourceOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/hour")
async def add_recording_source(
    document_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    context_label: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Attach a meeting recording (audio or video) to this document. The
    most expensive operation in this feature (Gemini Files API upload +
    processing + generateContent, same cost class as Video Walkthrough) —
    rate-limited, size-capped (SOW_MAX_RECORDING_MB, default 300MB), and
    duration-capped (SOW_MAX_RECORDING_MINUTES, default 60min) via ffprobe
    before the file is ever registered as an artifact."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    file_name = (file.filename or "recording.mp4")[:500]
    from app.services.sow_ledger import recording_duration_seconds, recording_mime_for

    try:
        recording_mime_for(file_name)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    content = await file.read()
    max_bytes = _max_recording_bytes()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Recording exceeds the {max_bytes // (1024 * 1024)}MB limit.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Recording file is empty")

    sha = hashlib.sha256(content).hexdigest()
    artifact = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.sha256 == sha,
            DesignArtifact.project_id == doc.project_id,
            DesignArtifact.artifact_type == ArtifactType.meeting_recording,
        )
        .first()
    )
    if artifact is None:
        # Duration cap check BEFORE this file is ever registered as an
        # artifact: write to a temp path first so ffprobe can measure it,
        # only promote to permanent storage if it passes.
        ext = os.path.splitext(file_name.lower())[1]
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            duration_s = recording_duration_seconds(tmp_path)
            max_minutes = _max_recording_minutes()
            if duration_s is not None and duration_s > max_minutes * 60:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Recording is {duration_s / 60:.0f} minutes, which exceeds the "
                        f"{max_minutes}-minute limit. Trim it and re-upload."
                    ),
                )

            recording_dir = os.path.join(_data_dir(), "sow_meeting_recording")
            os.makedirs(recording_dir, exist_ok=True)
            storage_path = os.path.join(recording_dir, f"{sha}{ext}")
            shutil.move(tmp_path, storage_path)
            tmp_path = None  # moved — nothing left for the finally block to clean up
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        artifact = DesignArtifact(
            project_id=doc.project_id,
            artifact_type=ArtifactType.meeting_recording,
            file_name=file_name,
            sha256=sha,
            storage_path=storage_path,
            platform_name=(context_label or "").strip()[:300] or None,
            parse_status=ParseStatus.not_required,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

    source = _attach_source(db, doc, artifact, current_user)

    from app.workers.tasks.sow_ledger import extract_recording_ledger_task

    extract_recording_ledger_task.delay(str(source.id))
    logger.info(
        "SOW document %s: recording source %s attached by %s", doc.id, source.id, current_user.id
    )
    return _source_out(db, source)


@router.post(
    "/documents/{document_id}/sources/design",
    response_model=SowDocumentSourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_design_source(
    document_id: uuid.UUID,
    request: Request,
    artifact_id: uuid.UUID | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    target_page: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Attach a design reference — either an existing figma_png artifact
    (already uploaded via Vibe Testing's References panel or Figma import;
    pass artifact_id) or a freshly uploaded PNG (exactly one of the two)."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    has_artifact_id = artifact_id is not None
    has_file = file is not None
    if has_artifact_id == has_file:  # both or neither
        raise HTTPException(
            status_code=400,
            detail="Provide either an existing artifact_id or a new PNG file, not both/neither.",
        )

    if has_artifact_id:
        artifact = db.get(DesignArtifact, artifact_id)
        if artifact is None or artifact.artifact_type != ArtifactType.figma_png:
            raise HTTPException(status_code=404, detail="Design reference not found")
    else:
        file_name = (file.filename or "design.png")[:500]
        content = await file.read()
        if len(content) > _MAX_DESIGN_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Design image exceeds the {_MAX_DESIGN_IMAGE_BYTES // (1024 * 1024)}MB limit.",
            )
        if not content or not content.startswith(_PNG_MAGIC):
            raise HTTPException(status_code=400, detail="File is not a valid PNG")

        sha = hashlib.sha256(content).hexdigest()
        artifact = (
            db.query(DesignArtifact)
            .filter(
                DesignArtifact.sha256 == sha,
                DesignArtifact.project_id == doc.project_id,
                DesignArtifact.artifact_type == ArtifactType.figma_png,
            )
            .first()
        )
        if artifact is None:
            design_dir = os.path.join(_data_dir(), "sow_design_ref")
            os.makedirs(design_dir, exist_ok=True)
            storage_path = os.path.join(design_dir, f"{sha}.png")
            with open(storage_path, "wb") as fh:
                fh.write(content)

            artifact = DesignArtifact(
                project_id=doc.project_id,
                artifact_type=ArtifactType.figma_png,
                file_name=file_name,
                sha256=sha,
                storage_path=storage_path,
                target_page=(target_page or "").strip()[:1000] or None,
                parse_status=ParseStatus.not_required,
            )
            db.add(artifact)
            db.commit()
            db.refresh(artifact)

    source = _attach_source(db, doc, artifact, current_user)

    from app.workers.tasks.sow_ledger import extract_design_ledger_task

    extract_design_ledger_task.delay(str(source.id))
    logger.info(
        "SOW document %s: design source %s attached by %s", doc.id, source.id, current_user.id
    )
    return _source_out(db, source)


@router.get("/documents/{document_id}/sources", response_model=list[SowDocumentSourceOut])
def list_sources(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    sources = (
        db.query(SowDocumentSource)
        .filter(SowDocumentSource.document_id == doc.id)
        .order_by(SowDocumentSource.created_at.desc())
        .all()
    )
    return [_source_out(db, s) for s in sources]


@router.delete("/documents/{document_id}/sources/{source_id}", status_code=status.HTTP_200_OK)
def delete_source(
    document_id: uuid.UUID,
    source_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Detach a source: removes the attachment and this document's ledger
    facts that came from it. The underlying design_artifacts row (and any
    attachment of the same artifact to a DIFFERENT document) is left
    untouched — artifacts are shared, deduplicated storage, not owned by
    any one document."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    source = (
        db.query(SowDocumentSource)
        .filter(SowDocumentSource.id == source_id, SowDocumentSource.document_id == doc.id)
        .one_or_none()
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        if source.artifact_id is not None:
            db.query(SowRequirementsLedger).filter(
                SowRequirementsLedger.document_id == doc.id,
                SowRequirementsLedger.source_artifact_id == source.artifact_id,
            ).delete(synchronize_session=False)
        db.delete(source)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to delete SOW source %s", source_id)
        raise HTTPException(status_code=500, detail="Could not remove source")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="delete_sow_source",
        resource_type="sow_document_source",
        resource_id=str(source_id),
        details={"document_id": str(doc.id)},
        ip_address=_client_ip(request),
    )
    logger.info("SOW document %s: source %s removed by %s", doc.id, source_id, current_user.id)
    return {"message": "Source removed"}


@router.get("/documents/{document_id}/ledger", response_model=list[SowRequirementsLedgerOut])
def list_ledger(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    fact_type: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """The raw requirements-ledger dump Phase 1 promises: every extracted
    fact for this document, across every attached source. This is
    intentionally unstructured/ungrouped (Phase 2 formalizes grouping into
    SOW sections) — it exists so extraction quality can be inspected
    directly against the source material before anything is drafted from it."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    query = db.query(SowRequirementsLedger).filter(
        SowRequirementsLedger.document_id == doc.id,
        SowRequirementsLedger.superseded.is_(False),
    )
    if fact_type is not None:
        if fact_type not in _VALID_LEDGER_FACT_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid fact_type '{fact_type}'")
        query = query.filter(SowRequirementsLedger.fact_type == fact_type)

    rows = query.order_by(SowRequirementsLedger.created_at.asc()).limit(limit).all()
    return [
        SowRequirementsLedgerOut(
            id=r.id,
            document_id=r.document_id,
            source_artifact_id=r.source_artifact_id,
            fact_type=r.fact_type.value if hasattr(r.fact_type, "value") else str(r.fact_type),
            element_type=(
                r.element_type.value if r.element_type and hasattr(r.element_type, "value") else r.element_type
            ),
            label=r.label,
            location=r.location,
            behavior_notes=r.behavior_notes,
            source_ref=r.source_ref,
            superseded=r.superseded,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — generation (Pass 2 drafting + Pass 4 assembly), versions, sections
# ─────────────────────────────────────────────────────────────────────────────


def _job_out(job: SowGenerationJob) -> SowGenerationJobOut:
    return SowGenerationJobOut(
        id=job.id,
        document_id=job.document_id,
        version_id=job.version_id,
        stage=job.stage.value if hasattr(job.stage, "value") else str(job.stage),
        stage_progress=job.stage_progress,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


def _version_out(version: SowDocumentVersion) -> SowVersionOut:
    return SowVersionOut(
        id=version.id,
        document_id=version.document_id,
        version_number=version.version_number,
        kind=version.kind.value if hasattr(version.kind, "value") else str(version.kind),
        parent_version_id=version.parent_version_id,
        status=version.status.value if hasattr(version.status, "value") else str(version.status),
        error_message=version.error_message,
        generated_by_model=version.generated_by_model,
        created_at=version.created_at,
    )


def _section_out(section: SowSection) -> SowSectionOut:
    from app.services.sow_drafting import render_blocks_markdown

    return SowSectionOut(
        id=section.id,
        order_index=section.order_index,
        heading=section.heading,
        section_key=section.section_key,
        status=section.status.value if hasattr(section.status, "value") else str(section.status),
        error_message=section.error_message,
        content_blocks=section.content_blocks or [],
        rendered_markdown=render_blocks_markdown(section.content_blocks or []),
        coverage_score=section.coverage_score,
        coverage_gaps=section.coverage_gaps,
        edited_by_human=section.edited_by_human,
    )


@router.post(
    "/documents/{document_id}/generate",
    response_model=SowGenerationJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(_generate_rate_limit)
def generate_document(
    document_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Kick off a full generation: groups the current requirements ledger
    into sections (Pass 2a), drafts each independently (Pass 2b) plus the
    Project Overview/Scope of Work framing sections, and assembles the
    standard skeleton (Pass 4) — see app/workers/tasks/sow_generation.py.
    Always creates a NEW version (fresh full_generation lineage, never a
    patch — see SowDocumentVersion.parent_version_id's docstring); this is
    "regenerate everything," not an edit.

    Rate-limited (plan §11.8, SOW_GENERATE_RATE_LIMIT, default 10/hour) —
    added retroactively in Phase 7 alongside /rewrite; this was in the
    original plan for /generate too but was missed when Phase 3 shipped it.

    Concurrency guard (plan §11.3): the document row is locked for the
    duration of the check-and-set below so two concurrent generate calls
    can't both observe 'not generating' and both enqueue a job — the loser
    gets a 409 with nothing enqueued, instead of two jobs racing to write
    two divergent "next" versions.
    """
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    locked_doc = (
        db.query(SowDocument).filter(SowDocument.id == doc.id).with_for_update().one()
    )
    if locked_doc.status == SowDocumentStatus.generating:
        raise HTTPException(
            status_code=409,
            detail="A generation is already in progress for this document.",
        )

    fact_count = (
        db.query(SowRequirementsLedger)
        .filter(
            SowRequirementsLedger.document_id == doc.id,
            SowRequirementsLedger.superseded.is_(False),
        )
        .count()
    )
    if fact_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No requirements ledger facts yet — attach at least one source and "
            "wait for extraction to finish before generating.",
        )

    try:
        next_version_number = (
            db.query(func.max(SowDocumentVersion.version_number))
            .filter(SowDocumentVersion.document_id == doc.id)
            .scalar()
            or 0
        ) + 1

        version = SowDocumentVersion(
            document_id=doc.id,
            version_number=next_version_number,
            kind=SowVersionKind.full_generation,
            status=SowVersionStatus.pending,
        )
        db.add(version)
        # Flush (not commit) to populate version.id from its Python-side
        # default so the job row below can reference it, without yet
        # releasing the row lock or creating a window where the document
        # could be left permanently 'generating' if the job insert failed
        # after an earlier, separate commit — both rows and the status
        # change land in ONE commit, atomically, on purpose.
        db.flush()

        job = SowGenerationJob(
            document_id=doc.id,
            version_id=version.id,
            stage=SowJobStage.ledger_extraction,
            status=SowJobStatus.queued,
        )
        db.add(job)
        locked_doc.status = SowDocumentStatus.generating
        db.commit()
        db.refresh(version)
        db.refresh(job)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to start SOW generation for document %s", document_id)
        raise HTTPException(status_code=500, detail="Could not start generation")

    # Enqueue AFTER commit so the worker can always load the rows.
    from app.workers.tasks.sow_generation import generate_sow_task

    generate_sow_task.delay(str(doc.id), str(version.id))
    logger.info(
        "SOW document %s: generation started (version %s) by %s",
        doc.id, version.id, current_user.id,
    )
    return _job_out(job)


@router.post(
    "/documents/{document_id}/rewrite",
    response_model=SowGenerationJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(_generate_rate_limit)
def rewrite_document(
    document_id: uuid.UUID,
    payload: SowRewriteRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Phase 7 — the patch path (plan §5/§11.4): regenerate ONLY
    payload.target_sections, copying every other section forward
    unchanged from the current version into a new kind=patch version.
    Full regeneration is the unchanged POST .../generate — this is
    deliberately the other half of "rewrite supports both section-level
    patch (default) and full regeneration (on demand)" from the plan's
    confirmed scope.

    Framing sections (project-overview, scope-of-work) and the five
    templated trailing sections can't be targeted here — see
    app/services/sow_patch.py's module docstring for why; edit them
    directly via PATCH .../sections/{key} instead.

    Human-edit protection (plan §11.4): a targeted section that was hand-
    edited (edited_by_human=true) is copied through unchanged rather than
    regenerated, UNLESS its key is also listed in
    payload.override_manual_edits.

    Same concurrency guard and same single-atomic-commit discipline as
    POST .../generate (plan §11.3 and the split-commit lesson from that
    endpoint's own Phase 3 self-review).
    """
    from app.services.sow_patch import filter_protected_sections, non_patchable_section_keys

    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    bad_keys = sorted(set(payload.target_sections) & non_patchable_section_keys())
    if bad_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"These sections can't be regenerated via rewrite: {bad_keys}. Framing and "
                "templated sections aren't fact-backed the same way functional sections are — "
                "edit them directly via PATCH .../sections/{key} instead."
            ),
        )

    # Lock BEFORE reading current_version_id's sections, not after: a plain
    # unlocked read here (as an earlier draft of this endpoint did) leaves
    # a race window where a concurrent /generate could complete and move
    # current_version_id on between that read and this lock, so every
    # validation below would silently run against a version that's no
    # longer current by the time this transaction commits. Locking first
    # and re-reading current_version_id from the now-fresh, locked `doc`
    # object closes that window -- caught in self-review, not shipped.
    locked_doc = db.query(SowDocument).filter(SowDocument.id == doc.id).with_for_update().one()
    if locked_doc.status == SowDocumentStatus.generating:
        raise HTTPException(
            status_code=409, detail="A generation is already in progress for this document."
        )
    if locked_doc.current_version_id is None:
        raise HTTPException(
            status_code=400, detail="This document has no generated version to rewrite yet"
        )

    parent_sections = (
        db.query(SowSection)
        .filter(SowSection.version_id == locked_doc.current_version_id)
        .order_by(SowSection.order_index.asc())
        .all()
    )
    parent_sections_by_key = {s.section_key: s for s in parent_sections}

    missing_keys = [k for k in payload.target_sections if k not in parent_sections_by_key]
    if missing_keys:
        raise HTTPException(
            status_code=404, detail=f"Section(s) not found in the current version: {missing_keys}"
        )

    to_regenerate, skipped_protected = filter_protected_sections(
        payload.target_sections, parent_sections_by_key, payload.override_manual_edits
    )
    if not to_regenerate:
        raise HTTPException(
            status_code=400,
            detail=(
                "Every targeted section is hand-edited and protected — include it in "
                "override_manual_edits to force-regenerate it anyway, or choose a different "
                "section."
                if skipped_protected
                else "No sections to regenerate."
            ),
        )

    try:
        next_version_number = (
            db.query(func.max(SowDocumentVersion.version_number))
            .filter(SowDocumentVersion.document_id == doc.id)
            .scalar()
            or 0
        ) + 1

        version = SowDocumentVersion(
            document_id=doc.id,
            version_number=next_version_number,
            kind=SowVersionKind.patch,
            parent_version_id=locked_doc.current_version_id,
            status=SowVersionStatus.pending,
        )
        db.add(version)
        db.flush()

        # Copy every section forward unchanged EXCEPT the ones actually
        # being regenerated — this is what makes "everything else stays
        # exactly as it was" true, including sections skipped for
        # human-edit protection (copied, not regenerated, even though
        # the caller asked to target them).
        to_regen_set = set(to_regenerate)
        for parent_section in parent_sections:
            if parent_section.section_key in to_regen_set:
                continue
            db.add(SowSection(
                version_id=version.id,
                order_index=parent_section.order_index,
                heading=parent_section.heading,
                section_key=parent_section.section_key,
                content_blocks=parent_section.content_blocks,
                status=parent_section.status,
                error_message=parent_section.error_message,
                coverage_score=parent_section.coverage_score,
                coverage_gaps=parent_section.coverage_gaps,
                edited_by_human=parent_section.edited_by_human,
                edited_at=parent_section.edited_at,
            ))

        job = SowGenerationJob(
            document_id=doc.id,
            version_id=version.id,
            stage=SowJobStage.drafting,
            status=SowJobStatus.queued,
        )
        db.add(job)
        locked_doc.status = SowDocumentStatus.generating
        db.commit()
        db.refresh(version)
        db.refresh(job)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to start SOW rewrite for document %s", document_id)
        raise HTTPException(status_code=500, detail="Could not start rewrite")

    # Enqueue AFTER commit so the worker can always load the rows.
    from app.workers.tasks.sow_generation import patch_sow_task

    patch_sow_task.delay(str(doc.id), str(version.id), to_regenerate)
    logger.info(
        "SOW document %s: rewrite started (version %s, %d section(s)) by %s",
        doc.id, version.id, len(to_regenerate), current_user.id,
    )

    write_audit_log(
        db,
        user_id=current_user.id,
        action="sow_rewrite",
        resource_type="sow_document",
        resource_id=str(doc.id),
        details={
            "version_id": str(version.id),
            "target_sections": to_regenerate,
            "skipped_protected": skipped_protected,
        },
        ip_address=_client_ip(request),
    )

    return _job_out(job)


@router.get("/documents/{document_id}/generation", response_model=SowGenerationJobOut)
def get_generation_status(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll the most recent generation job for this document."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    job = (
        db.query(SowGenerationJob)
        .filter(SowGenerationJob.document_id == doc.id)
        .order_by(SowGenerationJob.created_at.desc())
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="No generation has been run for this document yet")
    return _job_out(job)


@router.get("/documents/{document_id}/versions", response_model=list[SowVersionOut])
def list_versions(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    versions = (
        db.query(SowDocumentVersion)
        .filter(SowDocumentVersion.document_id == doc.id)
        .order_by(SowDocumentVersion.version_number.desc())
        .all()
    )
    return [_version_out(v) for v in versions]


@router.get("/documents/{document_id}/versions/{version_id}", response_model=SowVersionDetailOut)
def get_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Version detail: every section, ordered, with both raw content_blocks
    (for future structured reuse — editor, export) and markdown rendered
    on demand (plan §11.7 — never stored, so it can never go stale)."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    version = (
        db.query(SowDocumentVersion)
        .filter(SowDocumentVersion.id == version_id, SowDocumentVersion.document_id == doc.id)
        .one_or_none()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    sections = (
        db.query(SowSection)
        .filter(SowSection.version_id == version.id)
        .order_by(SowSection.order_index.asc())
        .all()
    )
    return SowVersionDetailOut(
        **_version_out(version).model_dump(),
        sections=[_section_out(s) for s in sections],
    )


@router.patch(
    "/documents/{document_id}/sections/{section_key}",
    response_model=SowSectionOut,
)
def patch_section(
    document_id: uuid.UUID,
    section_key: str,
    payload: SowSectionPatch,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Manual hand-edit of one section's content_blocks (plan §5/§11.4).
    Always targets the document's CURRENT version — there's no version_id
    in the path, matching the read-only view's own default of "whatever
    current_version_id points at." Editing an older, non-current version
    is deliberately not supported: that would create ambiguity about which
    version is actually "the SOW" without the full patch/rewrite machinery
    (Phase 7) to reconcile it.

    Every block is re-validated server-side through the exact same
    _validate_block schema sow_drafting's LLM output goes through — never
    trust client-side validation alone; a malformed hand-edit is rejected
    with 422, not silently saved as something the renderer/export can't
    handle later. A block's own `fact_index` (if present) is preserved
    verbatim rather than cross-checked against a ledger-fact list — unlike
    LLM output, a human editor is a trusted source for that one field, and
    the original per-section fact list used at draft time isn't otherwise
    retrievable from just a section_key.

    Sets edited_by_human=True/edited_at=now() (plan §11.4). Note this
    protects nothing yet against a plain "Generate" click — every
    generation always creates a brand-new version from scratch (Phase 3's
    deliberate "regenerate everything, not an edit" design); the
    protection only becomes meaningful once Phase 7's rewrite/patch flow
    exists and can choose to skip regenerating sections flagged this way.
    The frontend surfaces a warning before Generate if the current version
    has any hand-edited sections, so this isn't a silent data-loss trap.

    Also clears coverage_score/coverage_gaps to null — a Phase 4 audit
    result describes the PRE-edit content; showing a stale score after a
    human edit would be more misleading than showing "not yet audited."

    If the section's status was 'error', a successful edit flips it to
    'done' and clears error_message (a human-authored fix is a real fix),
    then — if that was the last remaining errored section in this version
    — promotes the version from 'done_with_errors' back to 'done' and the
    document from 'error' back to 'ready', so the UI doesn't keep
    screaming about a problem the user already resolved by hand.
    """
    from app.services.sow_drafting import _validate_block

    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)

    if doc.current_version_id is None:
        raise HTTPException(
            status_code=404, detail="This document has no generated version to edit yet"
        )

    section = (
        db.query(SowSection)
        .filter(
            SowSection.version_id == doc.current_version_id,
            SowSection.section_key == section_key,
        )
        .one_or_none()
    )
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found in the current version")

    validated: list[dict] = []
    for raw_block in payload.content_blocks:
        idx = raw_block.get("fact_index") if isinstance(raw_block, dict) else None
        valid_indices = {idx} if isinstance(idx, int) else set()
        block = _validate_block(raw_block, valid_indices)
        if block is None:
            bad_type = raw_block.get("type", "(missing type)") if isinstance(raw_block, dict) else "(not an object)"
            raise HTTPException(status_code=422, detail=f"Invalid block, type={bad_type!r}")
        validated.append(block)

    was_error = section.status == SowSectionStatus.error

    try:
        section.content_blocks = validated
        section.edited_by_human = True
        section.edited_at = datetime.now(timezone.utc)
        section.coverage_score = None
        section.coverage_gaps = None
        if was_error:
            section.status = SowSectionStatus.done
            section.error_message = None
        db.commit()
        db.refresh(section)

        if was_error:
            version = db.get(SowDocumentVersion, doc.current_version_id)
            remaining_errors = (
                db.query(SowSection)
                .filter(
                    SowSection.version_id == version.id,
                    SowSection.status == SowSectionStatus.error,
                )
                .count()
            )
            if remaining_errors == 0 and version.status == SowVersionStatus.done_with_errors:
                version.status = SowVersionStatus.done
                version.error_message = None
                if doc.status == SowDocumentStatus.error:
                    doc.status = SowDocumentStatus.ready
                db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Failed to save hand-edit for SOW section %s (document %s)", section_key, document_id
        )
        raise HTTPException(status_code=500, detail="Could not save section edit")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="sow_section_edit",
        resource_type="sow_section",
        resource_id=str(section.id),
        details={"document_id": str(document_id), "section_key": section_key},
        ip_address=_client_ip(request),
    )

    return _section_out(section)


_EXPORT_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _current_version_sections(db: Session, doc: SowDocument) -> list[SowSection]:
    """Shared by export and send-to-checkpoints -- both need "every section
    of the current version, in document order"."""
    if doc.current_version_id is None:
        raise HTTPException(
            status_code=400, detail="This document has no generated version yet"
        )
    return (
        db.query(SowSection)
        .filter(SowSection.version_id == doc.current_version_id)
        .order_by(SowSection.order_index.asc())
        .all()
    )


@router.post("/documents/{document_id}/export")
def export_document(
    document_id: uuid.UUID,
    payload: SowExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
):
    """Phase 6 (plan §5/§11.7): renders the CURRENT version to the
    requested format and streams it back directly -- nothing is stored.
    A saved export file would go stale the instant a section is edited
    (Phase 5) or patched (Phase 7); regenerating on every request is the
    only way the export can never lie about what the document currently
    says. Cheap and unlimited (no rate limit) -- this is pure rendering
    of already-generated content, not a new LLM call."""
    _feature_enabled()
    doc = _get_active_document_or_404(db, document_id)
    sections = _current_version_sections(db, doc)

    from app.services import sow_export

    fmt = payload.format
    try:
        if fmt == "md":
            content = sow_export.render_document_markdown(doc.title, sections).encode("utf-8")
        elif fmt == "docx":
            content = sow_export.render_document_docx(doc.title, sections)
        else:  # pdf -- schema already restricts format to md|docx|pdf
            content = sow_export.render_document_pdf(doc.title, sections)
    except Exception:
        logger.exception("SOW export failed for document %s, format=%s", document_id, fmt)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not render {fmt} export — see server logs. If this is a PDF export "
                "and the container was rebuilt without weasyprint's system libraries "
                "(docker/Dockerfile.backend), that's the most likely cause."
            ),
        )

    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", doc.title).strip("-") or "sow-document"
    filename = f"{safe_title}.{fmt}"
    return Response(
        content=content,
        media_type=_EXPORT_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/documents/{document_id}/send-to-checkpoints", response_model=SowSendToCheckpointsOut)
def send_to_checkpoints(
    document_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("sow")),
    _vibe_perm: User = Depends(require_permission("vibe_testing")),
):
    """Phase 6 (plan §5): wraps the current version as a `sow`-type
    DesignArtifact and hands it to the EXISTING, unmodified checkpoint
    extraction pipeline (app/api/v1/visual_audit.py::upload_sow /
    app/workers/tasks/sow_ingest.py::ingest_sow_task) -- zero duplicate
    parsing logic, this endpoint only reproduces that upload path's exact
    artifact-creation contract (sha256 dedupe, storage under
    {data_dir}/sow/, enqueue-after-commit) using rendered markdown as the
    file content instead of an uploaded file.

    Requires BOTH "sow" (to act on this document at all) and
    "vibe_testing" (the target pipeline's own upload endpoint requires it
    — this endpoint shouldn't grant indirect access to a surface its
    caller doesn't otherwise hold). Also checks VISUAL_AUDIT_ENABLED
    (that pipeline's own feature flag, distinct from SOW_ENABLED) is on,
    so this can't create an artifact nothing is able to process.
    """
    _feature_enabled()
    if os.environ.get("VISUAL_AUDIT_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=400,
            detail="Vibe Testing / SOW Checkpoints is not enabled on this deployment (VISUAL_AUDIT_ENABLED)",
        )
    doc = _get_active_document_or_404(db, document_id)
    sections = _current_version_sections(db, doc)

    from app.services.sow_export import render_document_markdown

    content = render_document_markdown(doc.title, sections).encode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Generated document is empty, nothing to send")

    sha = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.sha256 == sha,
            DesignArtifact.project_id == doc.project_id,
            DesignArtifact.artifact_type == ArtifactType.sow,
        )
        .first()
    )
    if existing:
        if existing.parse_status == ParseStatus.error:
            existing.parse_status = ParseStatus.pending
            existing.parse_error = None
            db.commit()
            from app.workers.tasks.sow_ingest import ingest_sow_task

            ingest_sow_task.delay(str(existing.id))
        return SowSendToCheckpointsOut(
            artifact_id=existing.id,
            reused=existing.parse_status == ParseStatus.done,
            message=(
                "This exact document was already sent — reusing its checkpoint analysis."
                if existing.parse_status == ParseStatus.done
                else "This exact document was already sent — checkpoint extraction is in progress."
            ),
        )

    sow_dir = os.path.join(_data_dir(), "sow")
    os.makedirs(sow_dir, exist_ok=True)
    storage_path = os.path.join(sow_dir, f"{sha}.md")
    with open(storage_path, "wb") as fh:
        fh.write(content)

    artifact = DesignArtifact(
        project_id=doc.project_id,
        artifact_type=ArtifactType.sow,
        file_name=f"{doc.title[:490]}.md",
        sha256=sha,
        storage_path=storage_path,
        parse_status=ParseStatus.pending,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    from app.workers.tasks.sow_ingest import ingest_sow_task

    ingest_sow_task.delay(str(artifact.id))
    logger.info(
        "SOW document %s sent to checkpoints as artifact %s by %s",
        document_id, artifact.id, current_user.id,
    )

    write_audit_log(
        db,
        user_id=current_user.id,
        action="sow_send_to_checkpoints",
        resource_type="sow_document",
        resource_id=str(doc.id),
        details={"artifact_id": str(artifact.id)},
        ip_address=_client_ip(request),
    )

    return SowSendToCheckpointsOut(
        artifact_id=artifact.id,
        reused=False,
        message="Sent for checkpoint extraction — check the Vibe Testing / SOW Checkpoints tab.",
    )
