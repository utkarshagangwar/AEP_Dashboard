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
  POST   /sow                        upload a SOW document (txt/md/pdf)
  GET    /sow                        list uploaded SOW documents
  GET    /sow/{artifact_id}          SOW detail incl. checkpoints + parts
  POST   /sow/{artifact_id}/parts/{part_number}/analyze
                                      analyze one part of a chunked SOW
  DELETE /sow/{artifact_id}          delete a SOW document + its checkpoints
  DELETE /video/{artifact_id}        delete a walkthrough video + its checkpoints

Functional checkpoints extracted here are detailed prompt instructions
("skills") saved directly to the ai_skills table (see app.services.skill_store)
as soon as parsing finishes — no live browser run is needed to produce them.
They show up in the Vibe Testing "Skills" tab alongside recorded replay
skills, and can be run on demand from there.
"""
import hashlib
import os
import tempfile
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
    SowPart,
    VisualFinding,
    VisualRun,
    VisualRunStatus,
)
from app.services.doc_chunking import STRATEGY_HARD_SPLIT

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
    # Free-text requirement/checkpoint reference (New Vibe Test Phase 1 — UI
    # Test flow). Same field name/shape as the Functional Test flow's
    # linked_requirement (AIRunCreate) so both can eventually be reported
    # against one coverage view (Phase 6).
    linked_requirement: str | None = Field(default=None, max_length=500)


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
    linked_requirement: str | None = None
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
        linked_requirement=run.linked_requirement,
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
    total_parts: int = 1
    # True only immediately after an upload that matched an already-fully-
    # analyzed document (sha256 Memory Bank hit) — lets the UI show that no
    # AI credits were spent for this upload.
    reused: bool = False
    # User-declared product this video walks through (video uploads only —
    # mandatory there; always null for sow/figma_png).
    platform_name: str | None = None
    created_at: str


class CheckpointOut(BaseModel):
    type: str
    title: str
    # Rendered Role/Objective/Context/Instructions/Notes markdown for
    # functional checkpoints (what actually becomes the AI agent's goal
    # text); a short appearance claim for visual ones.
    description: str
    # Structured fields behind the rendered description — functional only,
    # empty/null for visual — so the UI can show real sections instead of a
    # flat paragraph.
    role: str | None = None
    objective: str | None = None
    context: str | None = None
    instructions: list[str] = []
    notes: list[str] = []
    page: str | None
    expected: str | None
    # None = fully specified and runnable. "needs_review"/"needs_design_flow"
    # mean the source document named this requirement but did not specify it
    # well enough to execute — the checkpoint is real, not ready. Optional
    # because checkpoints parsed before migration 0040 have no such key in
    # their stored JSONB.
    review_status: str | None = None
    review_reason: str | None = None

    # ── TDD classification (app.services.tdd_extraction, migration 0043) ──
    # All optional: checkpoints extracted before the v2 pipeline (and
    # anything the TDD_EXTRACTION_V2=0 legacy path produces) carry no such
    # keys in their stored JSONB, and the UI renders those as unclassified
    # rather than guessing.
    #
    # test_type is the one the reader cannot do without: a NEGATIVE
    # checkpoint passes when the system REFUSES the action, so it must never
    # be presented in the same visual register as a happy path.
    test_type: str | None = None       # positive | negative | edge
    category: str | None = None        # tdd_extraction.CATEGORIES code
    # stated = the document specifies this expectation; derived = inferred
    # from standard QA practice, so a failure may be a spec gap rather than a
    # product defect.
    grounding: str | None = None
    # Shared by every variant of one behaviour, so the UI can group a
    # behaviour's positive/negative/edge checkpoints together.
    behaviour_key: str | None = None
    priority: str | None = None        # smoke | sanity | regression
    # Variants the checkpoint's category REQUIRES but the model did not
    # produce — flagged by check_variant_coverage() in Python rather than
    # trusted from the model's own claim about its work.
    coverage_gap: list[str] = []
    # Other parts that stated this same behaviour and were merged into this
    # checkpoint (tdd_extraction Stage 6). Empty for the common case. Present
    # so a merge is visible rather than inferred from a checkpoint count that
    # no longer adds up — nothing is silently lost.
    merged_from_parts: list[int] = []
    # Lower-priority variants of this behaviour that Stage 4c dropped to keep
    # the behaviour under its ceiling. Zero for the common case. Surfaced so a
    # deliberate cap is visible rather than looking like the extractor simply
    # found nothing more.
    capped_variants: int = 0

    # ── Flow anchoring (app.services.flow_validation, Stage 4d) ──
    # Both empty for every checkpoint extracted without a project flow model,
    # which is all of them until the flow layer lands, and for every row
    # stored before this feature existed. The UI renders a checkpoint with no
    # precondition_state as unanchored rather than implying it starts from
    # nowhere.
    #
    # precondition_state is the state at which this checkpoint's first
    # instruction becomes possible; setup_path is the ordered chain of states
    # from the entry state to it — literally what a runner must do before
    # step 1 of this test means anything.
    precondition_state: str | None = None
    setup_path: list[str] = []


class PartOut(BaseModel):
    part_number: int
    total_parts: int
    status: str
    error: str | None
    checkpoint_count: int
    char_count: int
    preview: str

    # ── Chunk provenance (SOW_CHUNKING_PLAN Phase 3 / migration 0038) ──
    # Exposed so the chunker's behavior is assertable from the UI and from
    # the Robot Framework suites, not just from worker logs. Without these
    # on the API, a vibe test can only verify chunking by reading the
    # database directly, which is not a vibe test.
    #
    # All default to null: parts written before migration 0038 carry no
    # provenance and must keep rendering.
    #
    # heading_path -- the section this part covers, e.g.
    #   ["2. Functional Requirements", "2.1 Candidate List"].
    heading_path: list[str] = []
    # locator -- "p.12" / "§4.3.2" / "00:14:32", traceability into the source.
    locator: str | None = None
    # strategy -- which chunking strategy produced this part.
    strategy: str | None = None
    # degraded -- True when strategy == "hard_split", meaning this part had
    #   to be cut at an arbitrary point because a single unit (an unbroken
    #   paragraph, an oversized table row) exceeded the character budget on
    #   its own. Extraction quality at those boundaries is reduced. The UI
    #   renders this as a badge; suite 05_failure_surfacing.robot asserts on
    #   it.
    degraded: bool = False

    # ── Testability gate audit trail (migration 0043) ──
    # excluded_zones -- what tdd_extraction's Stage 0 decided was not product
    #   behaviour and never sent for extraction:
    #   [{heading, zone_kind, reason, char_count, classifier}].
    #   Surfaced because a filter nobody can see is a filter nobody can
    #   audit — "the extractor quietly decided your requirements section was
    #   a glossary" has to be noticeable from the UI.
    # coverage -- tdd_extraction.scorecard() for this part. The headline
    #   figure is negative_edge_ratio; below 0.40 the extractor has drifted
    #   back to happy-path-only output.
    # Both default to null/empty: parts analyzed before migration 0043, and
    # any part parsed with TDD_EXTRACTION_V2=0, hold NULL.
    excluded_zones: list[dict] = []
    coverage: dict | None = None


class SowDetailOut(SowOut):
    parsed_by_model: str | None = None
    checkpoints: list[CheckpointOut] = []
    parts: list[PartOut] = []


def _sow_out(db: Session, artifact: DesignArtifact, reused: bool = False) -> SowOut:
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
        total_parts=artifact.total_parts or 1,
        reused=reused,
        platform_name=artifact.platform_name,
        created_at=artifact.created_at.isoformat() if artifact.created_at else "",
    )


def _checkpoint_out(c: dict) -> CheckpointOut:
    return CheckpointOut(
        type=str(c.get("type", "functional")),
        title=str(c.get("title", ""))[:200],
        description=str(c["description"]),
        role=c.get("role"),
        objective=c.get("objective"),
        context=c.get("context"),
        instructions=[str(s) for s in (c.get("instructions") or [])],
        notes=[str(s) for s in (c.get("notes") or [])],
        page=c.get("page"),
        expected=c.get("expected"),
        review_status=c.get("review_status"),
        review_reason=c.get("review_reason"),
        test_type=c.get("test_type"),
        category=c.get("category"),
        grounding=c.get("grounding"),
        behaviour_key=c.get("behaviour_key"),
        priority=c.get("priority"),
        # Guard the type rather than trusting it: coverage_gap comes out of
        # stored JSONB, and rows written before migration 0043 have no key
        # at all while a legacy row could in principle hold anything.
        coverage_gap=[str(g) for g in (c.get("coverage_gap") or [])]
        if isinstance(c.get("coverage_gap"), list)
        else [],
        merged_from_parts=[p for p in (c.get("merged_from_parts") or []) if isinstance(p, int)]
        if isinstance(c.get("merged_from_parts"), list)
        else [],
        capped_variants=c["capped_variants"]
        if isinstance(c.get("capped_variants"), int)
        else 0,
    )


def _parts_out(db: Session, artifact: DesignArtifact) -> list["PartOut"]:
    """Per-part breakdown for the UI. Real SowPart rows for a chunked SOW;
    otherwise (video, which never chunks, or a document analyzed before
    per-part tracking existed) a single synthetic entry representing the
    whole document, built from its merged DesignRule — so "run all
    checkpoints as goals" has one uniform shape regardless of artifact type.
    """
    sow_parts = (
        db.query(SowPart)
        .filter(SowPart.artifact_id == artifact.id)
        .order_by(SowPart.part_number)
        .all()
    )
    if sow_parts:
        return [
            PartOut(
                part_number=p.part_number,
                total_parts=artifact.total_parts or 1,
                status=p.status.value if hasattr(p.status, "value") else str(p.status),
                error=p.error,
                checkpoint_count=len(p.checkpoints or []),
                char_count=p.char_count,
                preview=(p.content or "").strip()[:160],
                # heading_path is JSONB; guard the type rather than trusting
                # it, since rows predating migration 0038 hold NULL.
                heading_path=[str(h) for h in (p.heading_path or [])]
                if isinstance(p.heading_path, list)
                else [],
                locator=p.locator,
                strategy=p.strategy,
                degraded=(p.strategy == STRATEGY_HARD_SPLIT),
                # Same JSONB guard as heading_path above — NULL on every part
                # analyzed before migration 0043.
                excluded_zones=[z for z in (p.excluded_zones or []) if isinstance(z, dict)]
                if isinstance(p.excluded_zones, list)
                else [],
                coverage=p.coverage_json if isinstance(p.coverage_json, dict) else None,
            )
            for p in sow_parts
        ]

    rule = db.query(DesignRule).filter(DesignRule.artifact_id == artifact.id).first()
    status = (
        artifact.parse_status.value
        if hasattr(artifact.parse_status, "value")
        else str(artifact.parse_status)
    )
    checkpoints = rule.checkpoints if rule else None
    return [
        PartOut(
            part_number=1,
            total_parts=1,
            status=status,
            error=artifact.parse_error,
            checkpoint_count=len(checkpoints or []),
            char_count=0,
            preview="",
        )
    ]


# ── Live extraction progress ─────────────────────────────────────────────────

class IngestEventOut(BaseModel):
    sequence: int
    # Machine-readable stage key, so the UI picks an icon without
    # pattern-matching on the sentence.
    stage: str
    # running | done | skipped | error. `skipped` is distinct from `done`
    # because "the repair pass found nothing to repair" and "the repair pass
    # never ran" are different facts.
    status: str
    description: str
    # Null for document-level steps (reading, chunking, the cross-part merge),
    # which the UI renders unprefixed rather than attributing to a part.
    part_number: int | None = None
    detail: dict | None = None
    created_at: str


class IngestProgressOut(BaseModel):
    artifact_id: uuid.UUID
    # Mirrors the artifact so a poller can stop on its own without a second
    # request: pending/processing means keep polling.
    parse_status: str
    total_parts: int = 1
    events: list[IngestEventOut] = []


@router.get("/sow/{artifact_id}/progress", response_model=IngestProgressOut)
def get_sow_progress(
    artifact_id: uuid.UUID,
    after: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Steps that actually ran during this artifact's extraction.

    Deliberately NOT a fixed list of phases derived from SowPart rows. These
    rows are written by the code doing the work, so a stage that did not run
    produces no row and a stage that was skipped says so — see
    app/services/sow_progress.py for why that distinction is load-bearing.

    `after` returns only events past that sequence number, so a poll during a
    long ingest sends back the two new rows rather than the whole timeline
    every two seconds.
    """
    _feature_enabled()
    from app.models.visual_qa import SowIngestEvent

    artifact = db.get(DesignArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Document not found")

    rows = (
        db.query(SowIngestEvent)
        .filter(
            SowIngestEvent.artifact_id == artifact_id,
            SowIngestEvent.sequence > after,
        )
        .order_by(SowIngestEvent.sequence)
        .all()
    )
    return IngestProgressOut(
        artifact_id=artifact_id,
        parse_status=artifact.parse_status.value
        if hasattr(artifact.parse_status, "value")
        else str(artifact.parse_status),
        total_parts=artifact.total_parts or 1,
        events=[
            IngestEventOut(
                sequence=e.sequence,
                stage=e.stage,
                status=e.status,
                description=e.description,
                part_number=e.part_number,
                detail=e.detail if isinstance(e.detail, dict) else None,
                created_at=e.created_at.isoformat() if e.created_at else "",
            )
            for e in rows
        ],
    )


# ── Project UI naming reference ──────────────────────────────────────────────

class UiInventoryOut(BaseModel):
    """What app.services.ui_inventory read off a project's evidence.

    Read-only and diagnostic. There is no rebuild endpoint on purpose: the
    inventory rebuilds itself whenever the project's evidence set changes, so
    a manual rebuild button would exist only to re-run a vision call that is
    already up to date. If it is wrong, the fix is better evidence, not
    another build.
    """

    project_id: uuid.UUID
    # [{screen, controls[], fields[], nav[], messages[]}] — the structured
    # form, for a caller that wants the labels rather than the prose.
    screens: list[dict] = []
    screen_count: int = 0
    label_count: int = 0
    # Exactly the text handed to the extraction prompt, so what the model
    # actually saw is inspectable rather than inferred.
    rendered_text: str | None = None
    built_by_model: str | None = None
    # Why the last build produced nothing usable. "no usable evidence
    # uploaded" and "the vision call failed" need different responses, so
    # they are not collapsed into an empty result.
    build_error: str | None = None
    source_artifact_count: int = 0
    updated_at: str | None = None


@router.get("/projects/{project_id}/ui-inventory", response_model=UiInventoryOut)
def get_project_ui_inventory(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The UI naming reference built for this project, if one exists yet.

    Returns an empty inventory (200, not 404) for a project that has never
    been through extraction: "no inventory yet" is a normal state, not a
    missing resource, and the caller renders it the same either way.
    """
    _feature_enabled()
    from app.models.visual_qa import ProjectUiInventory

    row = (
        db.query(ProjectUiInventory)
        .filter(ProjectUiInventory.project_id == project_id)
        .one_or_none()
    )
    if row is None:
        return UiInventoryOut(project_id=project_id)

    screens = row.inventory_json if isinstance(row.inventory_json, list) else []
    return UiInventoryOut(
        project_id=project_id,
        screens=screens,
        screen_count=row.screen_count or 0,
        label_count=sum(
            len(s.get(k) or [])
            for s in screens
            if isinstance(s, dict)
            for k in ("controls", "fields", "nav", "messages")
        ),
        rendered_text=row.rendered_text,
        built_by_model=row.built_by_model,
        build_error=row.build_error,
        source_artifact_count=len(row.source_artifact_ids or []),
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
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
        # Genuine Memory Bank hit: an identical document was already fully
        # (or partially) analyzed — no new LLM call, and the UI can show that
        # this upload reused a saved skill instead of spending AI credits.
        return _sow_out(db, existing, reused=existing.parse_status == ParseStatus.done)

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
                checkpoints.append(_checkpoint_out(c))

    return SowDetailOut(
        **base.model_dump(),
        parsed_by_model=rule.parsed_by_model if rule else None,
        checkpoints=checkpoints,
        parts=_parts_out(db, artifact),
    )


@router.post("/sow/{artifact_id}/parts/{part_number}/analyze", response_model=SowDetailOut, status_code=202)
def analyze_sow_part(
    artifact_id: uuid.UUID,
    part_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Trigger analysis of one part of a chunked SOW. Only one part of a
    given document may be analyzing at a time — enforced here (authoritative)
    as well as client-side (disabled buttons)."""
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

    part = (
        db.query(SowPart)
        .filter(SowPart.artifact_id == artifact.id, SowPart.part_number == part_number)
        .one_or_none()
    )
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    if part.status not in (ParseStatus.pending, ParseStatus.error):
        status_label = part.status.value if hasattr(part.status, "value") else part.status
        raise HTTPException(
            status_code=409, detail=f"Part {part_number} is already {status_label}"
        )

    other_active = (
        db.query(SowPart)
        .filter(
            SowPart.artifact_id == artifact.id,
            SowPart.part_number != part_number,
            SowPart.status == ParseStatus.processing,
        )
        .first()
    )
    if other_active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Part {other_active.part_number} is currently being analyzed — "
                "wait for it to finish"
            ),
        )

    part.status = ParseStatus.processing
    part.error = None
    artifact.parse_status = ParseStatus.processing
    db.commit()

    # Enqueue AFTER commit so the worker can always load the rows
    from app.workers.tasks.sow_ingest import analyze_sow_part_task

    analyze_sow_part_task.delay(str(artifact.id), part_number)
    logger.info(
        "SOW %s part %d analysis triggered by %s", artifact.id, part_number, current_user.id
    )

    return get_sow(artifact_id, db=db, current_user=current_user)


@router.delete("/sow/{artifact_id}", status_code=200)
def delete_sow(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Delete a SOW document and its parsed checkpoints/parts (FK cascade)."""
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

    storage_path = artifact.storage_path
    db.delete(artifact)
    db.commit()

    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
        except OSError:
            logger.warning("SOW delete: could not remove file %s", storage_path)

    logger.info("SOW %s deleted by %s", artifact_id, current_user.id)
    return {"message": "SOW deleted"}


# ── Walkthrough videos (Phase 5) ─────────────────────────────────────────────

_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv")
# EBML header — shared by WebM and MKV, since WebM is a Matroska profile.
# .mkv is stored as-is and converted to MP4 at ingest time
# (video_ingest._prepare_for_upload): Gemini's Files API has no Matroska type.
_MATROSKA_MAGIC = b"\x1a\x45\xdf\xa3"


def _max_video_bytes() -> int:
    return int(os.environ.get("VISUAL_VIDEO_MAX_MB", "500")) * 1024 * 1024


# Walkthroughs are now allowed up to 500MB, so the upload can no longer be
# buffered in memory: `await file.read()` on a half-gigabyte body costs that
# much RAM per concurrent upload and a handful of them would OOM the API
# container. Stream it to disk in chunks instead, hashing as we go and
# aborting the moment the running total passes the cap — memory stays flat at
# one chunk regardless of file size, and an oversized upload is rejected
# without ever being fully written.
_VIDEO_CHUNK_BYTES = 1024 * 1024


def _looks_like_video(content: bytes, ext: str) -> bool:
    """Content-based sanity check (extension/content-type are client-controlled)."""
    if ext in (".webm", ".mkv"):
        return content.startswith(_MATROSKA_MAGIC)
    # MP4/MOV: 'ftyp' box appears at offset 4 in well-formed files
    return b"ftyp" in content[:16]


@router.post("/video", response_model=SowOut, status_code=202)
async def upload_video(
    file: UploadFile = File(...),
    # Mandatory: without a declared platform, the model has no anchor and
    # will fill the gap by inferring/assuming what product it's looking at
    # (see video_ingest._build_video_prompt) — an observed incident had it
    # extract checkpoints from unrelated on-screen text for exactly this
    # reason. Required at the API layer, not just the DB (column is nullable
    # for sow/figma_png rows).
    platform_name: str = Form(...),
    project_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    _feature_enabled()

    platform_name = platform_name.strip()[:300]
    if not platform_name:
        raise HTTPException(
            status_code=400,
            detail="Platform/product name is required — tell the AI what "
            "application this video walks through so it doesn't have to guess.",
        )

    file_name = (file.filename or "walkthrough.mp4")[:500]
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in _VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Video must be a .mp4, .webm, .mov, or .mkv file"
        )

    max_bytes = _max_video_bytes()
    video_dir = os.path.join(_data_dir(), "video")
    os.makedirs(video_dir, exist_ok=True)

    # Written into video_dir (not the system temp dir) so the final os.replace
    # below is an atomic same-filesystem rename rather than a second full copy.
    digest = hashlib.sha256()
    header = b""
    total = 0
    fd, tmp_path = tempfile.mkstemp(dir=video_dir, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = await file.read(_VIDEO_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Video exceeds the {max_bytes // (1024 * 1024)}MB "
                        "limit. Trim the walkthrough to the relevant screens.",
                    )
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                digest.update(chunk)
                fh.write(chunk)
        if not total or not _looks_like_video(header, ext):
            raise HTTPException(status_code=400, detail="File is not a valid video")
    except BaseException:
        os.unlink(tmp_path)
        raise

    sha = digest.hexdigest()
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
        os.unlink(tmp_path)  # same bytes already on disk under the sha name
        existing.platform_name = platform_name
        if existing.parse_status == ParseStatus.error:
            existing.parse_status = ParseStatus.pending
            existing.parse_error = None
            db.commit()
            from app.workers.tasks.video_ingest import ingest_video_task

            ingest_video_task.delay(str(existing.id))
        else:
            db.commit()
        return _sow_out(db, existing)

    storage_path = os.path.join(video_dir, f"{sha}{ext}")  # server-generated name
    os.replace(tmp_path, storage_path)

    artifact = DesignArtifact(
        project_id=project_id,
        artifact_type=ArtifactType.video,
        file_name=file_name,
        sha256=sha,
        storage_path=storage_path,
        platform_name=platform_name,
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
                checkpoints.append(_checkpoint_out(c))
    return SowDetailOut(
        **base.model_dump(),
        parsed_by_model=rule.parsed_by_model if rule else None,
        checkpoints=checkpoints,
        parts=_parts_out(db, artifact),
    )


@router.delete("/video/{artifact_id}", status_code=200)
def delete_video(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Delete a walkthrough video and its parsed checkpoints (FK cascade)."""
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

    storage_path = artifact.storage_path
    db.delete(artifact)
    db.commit()

    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
        except OSError:
            logger.warning("Video delete: could not remove file %s", storage_path)

    logger.info("Video %s deleted by %s", artifact_id, current_user.id)
    return {"message": "Video deleted"}


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
        linked_requirement=payload.linked_requirement,
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
