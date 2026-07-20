"""Android Vibe Testing — APK build management (upload/list/delete) and the
static device-profile catalog.

Kept separate from ai_runs.py (already ~1000+ lines) though namespaced under
the same /ai-testing prefix — this is the Android-specific slice of the
Vibe Testing surface. Run submission itself still goes through the existing
POST /api/ai-testing/runs (see ai_runs.py's AIRunCreate.platform field).
"""
import hashlib
import os
import shutil
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.logging import get_logger
from app.models.ai_runs import AndroidAppBuild
from app.models.user import User
from app.schemas.ai_runs import AndroidAppBuildResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/ai-testing/android", tags=["ai-testing-android"])

_ZIP_MAGIC = b"PK\x03\x04"  # APK/AAB are ZIP archives
_CHUNK_SIZE = 1024 * 1024  # 1MB — bounds peak memory regardless of APK size


def _max_apk_bytes() -> int:
    return int(os.environ.get("ANDROID_MAX_APK_MB", "200")) * 1024 * 1024


def _builds_dir() -> str:
    from app.workers.tasks.visual_audit import data_dir

    path = os.path.join(data_dir(), "android_builds")
    os.makedirs(path, exist_ok=True)
    return path


def _build_out(b: AndroidAppBuild) -> AndroidAppBuildResponse:
    return AndroidAppBuildResponse(
        id=b.id,
        name=b.name,
        project_id=b.project_id,
        apk_filename=b.apk_filename,
        file_size=b.file_size,
        farm_vendor=b.farm_vendor,
        package_name=b.package_name,
        created_at=b.created_at,
    )


@router.post(
    "/builds", response_model=AndroidAppBuildResponse, status_code=status.HTTP_201_CREATED
)
async def upload_build(
    file: UploadFile = File(...),
    name: str = Form(...),
    project_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Upload an APK/AAB, push it to the device farm, and save it as a
    reusable AndroidAppBuild.

    Streams the upload into a temp file in fixed-size chunks (rather than
    buffering the whole file in memory via a single `await file.read()`,
    the pattern used elsewhere in visual_audit.py) — APKs run materially
    larger than the other Visual QA upload types, and this backend runs
    under a real RAM ceiling in production.
    """
    filename = (file.filename or "app.apk")[:500]
    ext = os.path.splitext(filename.lower())[1]
    if ext not in (".apk", ".aab"):
        raise HTTPException(status_code=400, detail="File must be a .apk or .aab")

    max_bytes = _max_apk_bytes()
    hasher = hashlib.sha256()
    total = 0
    first_chunk = b""

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = tmp.name
    try:
        try:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"APK exceeds the {max_bytes // (1024 * 1024)}MB limit",
                    )
                if not first_chunk:
                    first_chunk = chunk
                hasher.update(chunk)
                tmp.write(chunk)
        finally:
            tmp.close()

        # Content-based validation (extension/content-type are client-controlled).
        if not first_chunk.startswith(_ZIP_MAGIC):
            raise HTTPException(
                status_code=400, detail="File is not a valid APK/AAB (not a ZIP archive)"
            )
        if total == 0:
            raise HTTPException(status_code=400, detail="File is empty")

        sha = hasher.hexdigest()
        # Dedupe scoped by (sha256, project_id), same convention as
        # visual_audit.py's reference/SOW/video uploads.
        existing = (
            db.query(AndroidAppBuild)
            .filter(AndroidAppBuild.sha256 == sha, AndroidAppBuild.project_id == project_id)
            .first()
        )
        if existing:
            return _build_out(existing)

        storage_path = os.path.join(_builds_dir(), f"{sha}{ext}")
        shutil.move(tmp_path, storage_path)
        tmp_path = None  # moved — don't remove it in the finally below
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    from app.services import device_farm

    try:
        with open(storage_path, "rb") as fh:
            farm_result = device_farm.upload_apk(fh, filename)
    except device_farm.DeviceFarmError as exc:
        os.remove(storage_path)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    build = AndroidAppBuild(
        name=(name or "").strip()[:300] or filename,
        project_id=project_id,
        apk_filename=filename,
        sha256=sha,
        storage_path=storage_path,
        file_size=total,
        farm_app_id=farm_result["app_url"],
        created_by=current_user.id,
    )
    db.add(build)
    db.commit()
    db.refresh(build)
    logger.info(
        "Android app build %s uploaded by %s (farm_app_id=%s)",
        build.id,
        current_user.id,
        build.farm_app_id,
    )
    return _build_out(build)


@router.get("/builds", response_model=list[AndroidAppBuildResponse])
def list_builds(
    project_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(AndroidAppBuild)
    if project_id:
        q = q.filter(AndroidAppBuild.project_id == project_id)
    builds = q.order_by(AndroidAppBuild.created_at.desc()).limit(100).all()
    return [_build_out(b) for b in builds]


@router.delete("/builds/{build_id}", status_code=status.HTTP_200_OK)
def delete_build(
    build_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("vibe_testing")),
):
    """Delete a build row + its local APK file. Does not call BrowserStack's
    delete-app API — its own ~30-day inactivity expiry handles farm-side
    cleanup, and a build referenced by past runs should stay visible in
    run history regardless (android_app_build_name is denormalized onto
    the run for exactly this reason)."""
    build = db.get(AndroidAppBuild, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="Android app build not found")

    storage_path = build.storage_path
    db.delete(build)
    db.commit()

    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
        except OSError:
            logger.warning(
                "Android build delete: could not remove file %s", storage_path
            )

    logger.info("Android app build %s deleted by %s", build_id, current_user.id)
    return {"message": "Android app build deleted"}


@router.get("/device-profiles")
def list_device_profiles(
    current_user: User = Depends(get_current_user),
):
    from app.services.device_farm import DEVICE_PROFILES

    return [
        {"id": key, "label": profile["label"]} for key, profile in DEVICE_PROFILES.items()
    ]
