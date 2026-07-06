"""Visual Audit API (Phase 2) — reference uploads, runs, findings, images.

Feature-flagged: every endpoint returns 404 unless VISUAL_AUDIT_ENABLED=true,
so existing deployments see zero behavior change until explicitly opted in.

Endpoints (all under /api/v1/visual-audits):
  POST   /references                 upload a reference design PNG
  GET    /references                 list uploaded references
  POST   /                           create + enqueue a visual audit run
  GET    /                           list recent runs
  GET    /{run_id}                   run detail incl. findings
  POST   /{run_id}/cancel            cancel a pending run
  GET    /{run_id}/images/{kind}     stream reference|screenshot|diff image
"""
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.user import User
from app.models.visual_qa import (
    ArtifactType,
    DesignArtifact,
    DesignRule,
    ParseStatus,
    VisualFinding,
    VisualRun,
    VisualRunStatus,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/visual-audits", tags=["visual-audit"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB per reference image
_MAX_SOW_BYTES = 15 * 1024 * 1024     # 15MB per SOW document
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PDF_MAGIC = b"%PDF"
_SOW_EXTENSIONS = (".txt", ".md", ".pdf")


def _feature_enabled() -> None:
    """Gate every endpoint behind VISUAL_AUDIT_ENABLED (default: off)."""
    if os.environ.get("VISUAL_AUDIT_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Visual audit is not enabled")


def _data_dir() -> str:
    from app.workers.tasks.visual_audit import data_dir

    return data_dir()


# ── Schemas ──────────────────────────────────────────────────────────────────

class ReferenceOut(BaseModel):
    id: uuid.UUID
    file_name: str
    target_page: str | None
    project_id: uuid.UUID | None
    created_at: str
    # 'not_required' (direct upload) or 'done' = usable; 'pending'/'error' =
    # Figma frame still downloading / failed. UI filters on this.
    parse_status: str = "not_required"
    parse_error: str | None = None


def _reference_out(a: DesignArtifact) -> ReferenceOut:
    return ReferenceOut(
        id=a.id,
        file_name=a.file_name,
        target_page=a.target_page,
        project_id=a.project_id,
        created_at=a.created_at.isoformat() if a.created_at else "",
        parse_status=a.parse_status.value
        if hasattr(a.parse_status, "value")
        else str(a.parse_status),
        parse_error=a.parse_error,
    )


class RunCreate(BaseModel):
    target_url: HttpUrl
    artifact_id: uuid.UUID
    project_id: uuid.UUID | None = None
    environment: str | None = Field(default=None, max_length=200)


class FindingOut(BaseModel):
    engine: str
    severity: str
    element: str | None
    issue: str
    expected: str | None
    actual: str | None
    region: dict | None


class RunOut(BaseModel):
    id: uuid.UUID
    target_url: str
    artifact_id: uuid.UUID | None
    environment: str | None
    status: str
    pixel_mismatch_pct: int | None
    summary: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: str
    findings: list[FindingOut] = []


def _run_out(run: VisualRun, findings: list[VisualFinding] | None = None) -> RunOut:
    return RunOut(
        id=run.id,
        target_url=run.target_url,
        artifact_id=run.artifact_id,
        environment=run.environment,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        pixel_mismatch_pct=run.pixel_mismatch_pct,
        summary=run.summary,
        error_message=run.error_message,
        duration_ms=run.duration_ms,
        created_at=run.created_at.isoformat() if run.created_at else "",
        findings=[
            FindingOut(
                engine=f.engine.value if hasattr(f.engine, "value") else str(f.engine),
                severity=f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                element=f.element,
                issue=f.issue,
                expected=f.expected,
                actual=f.actual,
                region=f.region,
            )
            for f in (findings or [])
        ],
    )


class SowOut(BaseModel):
    id: uuid.UUID
    file_name: str
    project_id: uuid.UUID | None
    parse_status: str
    parse_error: str | None
    checkpoint_count: int
    created_at: str


class CheckpointOut(BaseModel):
    type: str
    title: str
    description: str
    page: str | None
    expected: str | None


class SowDetailOut(SowOut):
    parsed_by_model: str | None = None
    checkpoints: list[CheckpointOut] = []


def _sow_out(db: Session, artifact: DesignArtifact) -> SowOut:
    count = (
        db.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).count()
    )
    checkpoint_count = 0
    if count:
        rule = (
            db.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
        )
        checkpoint_count = len(rule.checkpoints or [])
    return SowOut(
        id=artifact.id,
        file_name=artifact.file_name,
        project_id=artifact.project_id,
        parse_status=artifact.parse_status.value
        if hasattr(artifact.parse_status, "value")
        else str(artifact.parse_status),
        parse_error=artifact.parse_error,
        checkpoint_count=checkpoint_count,
        created_at=artifact.created_at.isoformat() if artifact.created_at else "",
    )


# ── Reference uploads ────────────────────────────────────────────────────────

@router.post("/references", response_model=ReferenceOut, status_code=201)
async def upload_reference(
    file: UploadFile = File(...),
    target_page: str | None = Form(default=None),
    project_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Reference image exceeds 10MB limit")
    # Validate by content, not extension/content-type (both are client-controlled)
    if not content.startswith(_PNG_MAGIC):
        raise HTTPException(status_code=400, detail="Only PNG images are accepted")

    sha = hashlib.sha256(content).hexdigest()
    # Memory Bank dedupe: identical file already ingested → reuse it
    existing = (
        db.query(DesignArtifact)
        .filter(DesignArtifact.sha256 == sha, DesignArtifact.project_id == project_id)
        .first()
    )
    if existing:
        return _reference_out(existing)

    ref_dir = os.path.join(_data_dir(), "references")
    os.makedirs(ref_dir, exist_ok=True)
    # Server-generated filename — never trust the client's
    storage_path = os.path.join(ref_dir, f"{sha}.png")
    with open(storage_path, "wb") as fh:
        fh.write(content)

    artifact = DesignArtifact(
        project_id=project_id,
        artifact_type=ArtifactType.figma_png,
        file_name=(file.filename or "reference.png")[:500],
        sha256=sha,
        storage_path=storage_path,
        target_page=(target_page or None),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    logger.info("Visual audit: reference %s uploaded by %s", artifact.id, current_user.id)
    return _reference_out(artifact)


@router.get("/references", response_model=list[ReferenceOut])
def list_references(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    artifacts = (
        db.query(DesignArtifact)
        .filter(DesignArtifact.artifact_type == ArtifactType.figma_png)
        .order_by(DesignArtifact.created_at.desc())
        .limit(100)
        .all()
    )
    return [_reference_out(a) for a in artifacts]


# ── Figma import (Phase 4b) ─────────────────────────────────────────────────

class FigmaFrame(BaseModel):
    node_id: str = Field(max_length=100)
    name: str = Field(max_length=200)
    page: str | None = Field(default=None, max_length=200)


class FigmaImportRequest(BaseModel):
    file: str = Field(max_length=1000)  # Figma URL or raw file key
    frames: list[FigmaFrame] = Field(min_length=1, max_length=20)
    project_id: uuid.UUID | None = None


@router.get("/figma/frames")
def list_figma_frames(
    file: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List top-level frames of a Figma file (token stays server-side)."""
    _feature_enabled()
    from app.services import figma_service

    try:
        file_key = figma_service.parse_file_key(file)
        frames = figma_service.list_frames(file_key)
    except figma_service.FigmaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"file_key": file_key, "frames": frames}


@router.post("/figma/import", response_model=list[ReferenceOut], status_code=202)
def import_figma_frames(
    payload: FigmaImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Queue selected frames for download as reference designs."""
    _feature_enabled()
    from app.services import figma_service
    from app.workers.tasks.figma_import import (
        import_figma_frames_task,
        provisional_sha,
    )

    try:
        file_key = figma_service.parse_file_key(payload.file)
    except figma_service.FigmaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    results: list[DesignArtifact] = []
    artifact_map: dict[str, str] = {}  # node_id -> artifact_id (only new ones)
    for frame in payload.frames:
        sha = provisional_sha(file_key, frame.node_id)
        # Dedupe: same frame already imported (either still on its provisional
        # sha, or completed earlier — completed rows keep target_page match).
        existing = (
            db.query(DesignArtifact)
            .filter(
                DesignArtifact.sha256 == sha,
                DesignArtifact.project_id == payload.project_id,
            )
            .first()
        )
        if existing:
            results.append(existing)
            if existing.parse_status == ParseStatus.error:
                existing.parse_status = ParseStatus.pending
                existing.parse_error = None
                artifact_map[frame.node_id] = str(existing.id)  # retry download
            continue

        artifact = DesignArtifact(
            project_id=payload.project_id,
            artifact_type=ArtifactType.figma_png,
            file_name=f"{frame.name}.png"[:500],
            sha256=sha,  # provisional; replaced with content sha after download
            storage_path="",  # set by the worker once the PNG is on disk
            target_page=(frame.page or frame.name)[:1000],
            parse_status=ParseStatus.pending,
        )
        db.add(artifact)
        results.append(artifact)

    db.commit()
    for artifact in results:
        db.refresh(artifact)
    # Map node_ids for newly created rows (provisional sha ties them together)
    for frame in payload.frames:
        sha = provisional_sha(file_key, frame.node_id)
        for artifact in results:
            if artifact.sha256 == sha and frame.node_id not in artifact_map:
                if artifact.parse_status == ParseStatus.pending:
                    artifact_map[frame.node_id] = str(artifact.id)

    if artifact_map:
        # Enqueue AFTER commit so the worker can always load the rows
        import_figma_frames_task.delay(file_key, artifact_map)
        logger.info(
            "Figma import: %d frame(s) queued from %s by %s",
            len(artifact_map),
            file_key,
            current_user.id,
        )
    return [_reference_out(a) for a in results]


# ── SOW documents (Phase 3 — The Brain) ─────────────────────────────────────

@router.post("/sow", response_model=SowOut, status_code=202)
async def upload_sow(
    file: UploadFile = File(...),
    project_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    file_name = (file.filename or "sow.txt")[:500]
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in _SOW_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="SOW must be a .txt, .md, or .pdf file"
        )

    content = await file.read()
    if len(content) > _MAX_SOW_BYTES:
        raise HTTPException(status_code=413, detail="SOW exceeds 15MB limit")
    # Content-based validation (extension and content-type are client-controlled)
    if ext == ".pdf" and not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Document is empty")

    sha = hashlib.sha256(content).hexdigest()
    # Memory Bank dedupe: same document already ingested → reuse (and its
    # parsed checkpoints, if done) instead of paying tokens again.
    existing = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.sha256 == sha,
            DesignArtifact.project_id == project_id,
            DesignArtifact.artifact_type == ArtifactType.sow,
        )
        .first()
    )
    if existing:
        if existing.parse_status == ParseStatus.error:
            # Previous parse failed (e.g. provider outage) — re-enqueue.
            existing.parse_status = ParseStatus.pending
            existing.parse_error = None
            db.commit()
            from app.workers.tasks.sow_ingest import ingest_sow_task

            ingest_sow_task.delay(str(existing.id))
        return _sow_out(db, existing)

    sow_dir = os.path.join(_data_dir(), "sow")
    os.makedirs(sow_dir, exist_ok=True)
    storage_path = os.path.join(sow_dir, f"{sha}{ext}")  # server-generated name
    with open(storage_path, "wb") as fh:
        fh.write(content)

    artifact = DesignArtifact(
        project_id=project_id,
        artifact_type=ArtifactType.sow,
        file_name=file_name,
        sha256=sha,
        storage_path=storage_path,
        parse_status=ParseStatus.pending,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    # Enqueue AFTER commit so the worker can always load the row
    from app.workers.tasks.sow_ingest import ingest_sow_task

    ingest_sow_task.delay(str(artifact.id))
    logger.info("SOW %s uploaded by %s, ingestion enqueued", artifact.id, current_user.id)
    return _sow_out(db, artifact)


@router.get("/sow", response_model=list[SowOut])
def list_sows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    artifacts = (
        db.query(DesignArtifact)
        .filter(DesignArtifact.artifact_type == ArtifactType.sow)
        .order_by(DesignArtifact.created_at.desc())
        .limit(50)
        .all()
    )
    return [_sow_out(db, a) for a in artifacts]


@router.get("/sow/{artifact_id}", response_model=SowDetailOut)
def get_sow(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    artifact = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.id == artifact_id,
            DesignArtifact.artifact_type == ArtifactType.sow,
        )
        .one_or_none()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="SOW not found")

    base = _sow_out(db, artifact)
    rule = db.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
    checkpoints = []
    if rule:
        for c in rule.checkpoints or []:
            if isinstance(c, dict) and c.get("description"):
                checkpoints.append(
                    CheckpointOut(
                        type=str(c.get("type", "functional")),
                        title=str(c.get("title", ""))[:200],
                        description=str(c["description"]),
                        page=c.get("page"),
                        expected=c.get("expected"),
                    )
                )
    return SowDetailOut(
        **base.model_dump(),
        parsed_by_model=rule.parsed_by_model if rule else None,
        checkpoints=checkpoints,
    )


# ── Walkthrough videos (Phase 5) ─────────────────────────────────────────────

_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov")
_WEBM_MAGIC = b"\x1a\x45\xdf\xa3"


def _max_video_bytes() -> int:
    return int(os.environ.get("VISUAL_VIDEO_MAX_MB", "100")) * 1024 * 1024


def _looks_like_video(content: bytes, ext: str) -> bool:
    """Content-based sanity check (extension/content-type are client-controlled)."""
    if ext == ".webm":
        return content.startswith(_WEBM_MAGIC)
    # MP4/MOV: 'ftyp' box appears at offset 4 in well-formed files
    return b"ftyp" in content[:16]


@router.post("/video", response_model=SowOut, status_code=202)
async def upload_video(
    file: UploadFile = File(...),
    project_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    file_name = (file.filename or "walkthrough.mp4")[:500]
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in _VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Video must be a .mp4, .webm, or .mov file"
        )

    content = await file.read()
    max_bytes = _max_video_bytes()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Video exceeds the {max_bytes // (1024 * 1024)}MB limit. "
            "Trim the walkthrough to the relevant screens.",
        )
    if not content or not _looks_like_video(content, ext):
        raise HTTPException(status_code=400, detail="File is not a valid video")

    sha = hashlib.sha256(content).hexdigest()
    # Memory Bank dedupe — the whole point of Phase 5's cost control: the
    # same video is never uploaded to Gemini (or billed) twice.
    existing = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.sha256 == sha,
            DesignArtifact.project_id == project_id,
            DesignArtifact.artifact_type == ArtifactType.video,
        )
        .first()
    )
    if existing:
        if existing.parse_status == ParseStatus.error:
            existing.parse_status = ParseStatus.pending
            existing.parse_error = None
            db.commit()
            from app.workers.tasks.video_ingest import ingest_video_task

            ingest_video_task.delay(str(existing.id))
        return _sow_out(db, existing)

    video_dir = os.path.join(_data_dir(), "video")
    os.makedirs(video_dir, exist_ok=True)
    storage_path = os.path.join(video_dir, f"{sha}{ext}")  # server-generated name
    with open(storage_path, "wb") as fh:
        fh.write(content)

    artifact = DesignArtifact(
        project_id=project_id,
        artifact_type=ArtifactType.video,
        file_name=file_name,
        sha256=sha,
        storage_path=storage_path,
        parse_status=ParseStatus.pending,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    # Enqueue AFTER commit so the worker can always load the row
    from app.workers.tasks.video_ingest import ingest_video_task

    ingest_video_task.delay(str(artifact.id))
    logger.info("Video %s uploaded by %s, digestion enqueued", artifact.id, current_user.id)
    return _sow_out(db, artifact)


@router.get("/video", response_model=list[SowOut])
def list_videos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    artifacts = (
        db.query(DesignArtifact)
        .filter(DesignArtifact.artifact_type == ArtifactType.video)
        .order_by(DesignArtifact.created_at.desc())
        .limit(50)
        .all()
    )
    return [_sow_out(db, a) for a in artifacts]


@router.get("/video/{artifact_id}", response_model=SowDetailOut)
def get_video(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    artifact = (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.id == artifact_id,
            DesignArtifact.artifact_type == ArtifactType.video,
        )
        .one_or_none()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Video not found")

    base = _sow_out(db, artifact)
    rule = db.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
    checkpoints = []
    if rule:
        for c in rule.checkpoints or []:
            if isinstance(c, dict) and c.get("description"):
                checkpoints.append(
                    CheckpointOut(
                        type=str(c.get("type", "functional")),
                        title=str(c.get("title", ""))[:200],
                        description=str(c["description"]),
                        page=c.get("page"),
                        expected=c.get("expected"),
                    )
                )
    return SowDetailOut(
        **base.model_dump(),
        parsed_by_model=rule.parsed_by_model if rule else None,
        checkpoints=checkpoints,
    )


# ── Runs ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=RunOut, status_code=202)
def create_run(
    payload: RunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    artifact = (
        db.query(DesignArtifact)
        .filter(DesignArtifact.id == payload.artifact_id)
        .one_or_none()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Reference design not found")
    if artifact.artifact_type != ArtifactType.figma_png:
        raise HTTPException(
            status_code=400, detail="Selected artifact is not a reference image"
        )
    if artifact.parse_status not in (ParseStatus.not_required, ParseStatus.done):
        raise HTTPException(
            status_code=409,
            detail="Reference is still importing (or failed) — pick a ready one",
        )

    run = VisualRun(
        project_id=payload.project_id,
        environment=payload.environment,
        target_url=str(payload.target_url),
        artifact_id=artifact.id,
        status=VisualRunStatus.pending,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Enqueue AFTER commit so the worker can always load the row
    from app.workers.tasks.visual_audit import run_visual_audit_task

    run_visual_audit_task.delay(str(run.id))
    logger.info("Visual audit: run %s enqueued by %s", run.id, current_user.id)
    return _run_out(run)


@router.get("", response_model=list[RunOut])
def list_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    runs = db.query(VisualRun).order_by(VisualRun.created_at.desc()).limit(50).all()
    return [_run_out(r) for r in runs]


@router.get("/{run_id}", response_model=RunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    run = db.query(VisualRun).filter(VisualRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    findings = (
        db.query(VisualFinding)
        .filter(VisualFinding.run_id == run.id)
        .order_by(VisualFinding.severity, VisualFinding.created_at)
        .all()
    )
    return _run_out(run, findings)


@router.post("/{run_id}/cancel", response_model=RunOut)
def cancel_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()
    run = db.query(VisualRun).filter(VisualRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == VisualRunStatus.pending:
        run.status = VisualRunStatus.cancelled
        db.commit()
        db.refresh(run)
    return _run_out(run)


@router.get("/{run_id}/images/{kind}")
def get_run_image(
    run_id: uuid.UUID,
    kind: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _feature_enabled()
    run = db.query(VisualRun).filter(VisualRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Paths come from OUR database rows (server-generated), never client input,
    # and `kind` is matched against a fixed whitelist — no path traversal.
    if kind == "screenshot":
        path = run.screenshot_path
    elif kind == "diff":
        path = run.diff_image_path
    elif kind == "reference":
        artifact = (
            db.query(DesignArtifact)
            .filter(DesignArtifact.id == run.artifact_id)
            .one_or_none()
        )
        path = artifact.storage_path if artifact else None
    else:
        raise HTTPException(status_code=400, detail="kind must be reference|screenshot|diff")

    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not available")
    return FileResponse(path, media_type="image/png")
