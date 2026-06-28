"""Reports API — list, detail, export, and summary stats for test runs."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_roles
from app.core.logging import get_logger
from app.models.test_run import RunStatus, TestRun
from app.models.user import User, UserRole
from app.schemas.report import (
    ReportDetailResponse,
    ReportListResponse,
    ReportResultOut,
    ReportRunItem,
    ReportSummaryResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

STALE_RUN_THRESHOLD_MINUTES = 10


def _reconcile_stale_runs(db: Session) -> None:
    """Find and reconcile any runs stuck in running/queued past the threshold."""
    from app.api.v1.executions import _reconcile_run

    stale_runs = (
        db.query(TestRun)
        .filter(
            TestRun.status.in_([RunStatus.running, RunStatus.queued]),
            TestRun.started_at.isnot(None),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    for run in stale_runs:
        started = run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at
        if (now - started) > timedelta(minutes=STALE_RUN_THRESHOLD_MINUTES):
            logger.warning("Reconciling stale run %s (started_at=%s)", run.id, run.started_at)
            try:
                _reconcile_run(run, db)
            except Exception as exc:
                logger.error("Failed to reconcile run %s: %s", run.id, exc)


@router.get("/stats/summary", response_model=ReportSummaryResponse)
def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregate summary: total runs, pass rate, avg duration, runs per project (last 30 days)."""
    try:
        _reconcile_stale_runs(db)

        from sqlalchemy import text

        # Total runs and pass rate in last 30 days
        row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_runs,
                    COALESCE(AVG(CASE
                        WHEN started_at IS NOT NULL AND ended_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (ended_at - started_at)) * 1000
                    END), 0) AS avg_duration_ms
                FROM test_runs
                WHERE created_at >= NOW() - INTERVAL '30 days'
                """
            )
        ).fetchone()

        total_runs = int(row[0] or 0)
        avg_duration = int(float(row[1] or 0))

        result_row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_tests,
                    COALESCE(SUM(CASE WHEN tr.status = 'passed' THEN 1 ELSE 0 END), 0) AS passed_tests
                FROM test_results tr
                JOIN test_runs r ON tr.test_run_id = r.id
                WHERE r.created_at >= NOW() - INTERVAL '30 days'
                """
            )
        ).fetchone()

        total_tests = int(result_row[0] or 0)
        passed_tests = int(result_row[1] or 0)
        pass_rate = round((passed_tests / total_tests * 100), 1) if total_tests > 0 else 0.0

        # Runs per project
        project_rows = db.execute(
            text(
                """
                SELECT p.name AS project_name, COUNT(tr.id) AS run_count
                FROM test_runs tr
                JOIN test_suites ts ON tr.test_suite_id = ts.id
                JOIN projects p ON ts.project_id = p.id
                WHERE tr.created_at >= NOW() - INTERVAL '30 days'
                GROUP BY p.name
                ORDER BY run_count DESC
                """
            )
        ).fetchall()

        runs_per_project = [
            {"project_name": r[0], "run_count": int(r[1])} for r in project_rows
        ]

        logger.info("Report summary fetched by %s", current_user.id)
        return ReportSummaryResponse(
            total_runs=total_runs,
            pass_rate=pass_rate,
            avg_duration_ms=avg_duration,
            runs_per_project=runs_per_project,
        )
    except Exception as exc:
        logger.error("Failed to fetch report summary: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch report summary",
        )


@router.get("", response_model=ReportListResponse)
def list_reports(
    project_id: Optional[UUID] = Query(None),
    suite_id: Optional[UUID] = Query(None),
    run_status: Optional[str] = Query(None, alias="status"),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated list of test runs with summary stats."""
    from sqlalchemy import text

    try:
        _reconcile_stale_runs(db)

        offset = (page - 1) * limit
        where = "WHERE 1=1"
        params: dict = {}

        if project_id:
            params["project_id"] = str(project_id)
            where += " AND p.id = :project_id"
        if suite_id:
            params["suite_id"] = str(suite_id)
            where += " AND tr.test_suite_id = :suite_id"
        if run_status:
            params["run_status"] = run_status
            where += " AND tr.status = :run_status"
        if from_date:
            params["from_date"] = from_date
            where += " AND tr.created_at >= :from_date"
        if to_date:
            params["to_date"] = to_date
            where += " AND tr.created_at <= :to_date"

        # Count total
        count_row = db.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM test_runs tr
                LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
                LEFT JOIN projects p ON ts.project_id = p.id
                {where}
                """
            ),
            params,
        ).fetchone()
        total = int(count_row[0] or 0)

        # Fetch runs with aggregated result stats
        params["limit_val"] = limit
        params["offset_val"] = offset
        rows = db.execute(
            text(
                f"""
                SELECT tr.id, ts.name AS suite_name, ts.suite_type, p.name AS project_name,
                       p.id AS project_id, tr.status, u.full_name AS triggered_by_name,
                       tr.started_at, tr.ended_at, tr.created_at,
                       COALESCE(rs.total, 0) AS total,
                       COALESCE(rs.passed, 0) AS passed,
                       COALESCE(rs.failed, 0) AS failed,
                       COALESCE(
                           CASE
                               WHEN tr.started_at IS NOT NULL AND tr.ended_at IS NOT NULL
                               THEN EXTRACT(EPOCH FROM (tr.ended_at - tr.started_at)) * 1000
                               ELSE 0
                           END, 0
                       ) AS duration_ms
                FROM test_runs tr
                LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
                LEFT JOIN projects p ON ts.project_id = p.id
                LEFT JOIN users u ON tr.triggered_by = u.id
                LEFT JOIN (
                    SELECT test_run_id,
                           COUNT(*) AS total,
                           SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed,
                           SUM(CASE WHEN status = 'failed' OR status = 'error' THEN 1 ELSE 0 END) AS failed
                    FROM test_results
                    GROUP BY test_run_id
                ) rs ON rs.test_run_id = tr.id
                {where}
                ORDER BY tr.created_at DESC
                LIMIT :limit_val OFFSET :offset_val
                """
            ),
            params,
        ).fetchall()

        data = [
            ReportRunItem(
                id=r[0],
                suite_name=r[1],
                suite_type=r[2],
                project_name=r[3],
                project_id=r[4],
                status=r[5],
                triggered_by_name=r[6],
                started_at=r[7],
                ended_at=r[8],
                created_at=r[9],
                total=int(r[10] or 0),
                passed=int(r[11] or 0),
                failed=int(r[12] or 0),
                duration_ms=int(r[13] or 0),
            )
            for r in rows
        ]

        logger.info("Reports listed by %s (count=%d)", current_user.id, len(data))
        return ReportListResponse(data=data, total=total, page=page, limit=limit)
    except Exception as exc:
        logger.error("Failed to list reports: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list reports",
        )


@router.get("/{run_id}", response_model=ReportDetailResponse)
def get_report_detail(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full report: run metadata + all test results + defect count."""
    from sqlalchemy import text

    try:
        # Fetch run with joined info
        run_row = db.execute(
            text(
                """
                SELECT tr.id, tr.status, ts.name AS suite_name, ts.suite_type,
                       p.name AS project_name, u.full_name AS triggered_by_name,
                       tr.started_at, tr.ended_at, tr.created_at
                FROM test_runs tr
                LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
                LEFT JOIN projects p ON ts.project_id = p.id
                LEFT JOIN users u ON tr.triggered_by = u.id
                WHERE tr.id = :run_id
                """
            ),
            {"run_id": str(run_id)},
        ).fetchone()

        if not run_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        # Fetch test results with defect counts
        result_rows = db.execute(
            text(
                """
                SELECT tr.id, tr.test_name, tr.status, tr.duration_ms,
                       tr.error_message, tr.created_at,
                       COALESCE(d.defect_count, 0) AS defect_count,
                       tr.source_suite, tr.tags
                FROM test_results tr
                LEFT JOIN (
                    SELECT test_result_id, COUNT(*) AS defect_count
                    FROM defects
                    GROUP BY test_result_id
                ) d ON d.test_result_id = tr.id
                WHERE tr.test_run_id = :run_id
                ORDER BY
                    CASE tr.status WHEN 'error' THEN 1 WHEN 'failed' THEN 2
                         WHEN 'skipped' THEN 3 ELSE 4 END,
                    tr.created_at
                """
            ),
            {"run_id": str(run_id)},
        ).fetchall()

        # Aggregate stats
        total = len(result_rows)
        passed = sum(1 for r in result_rows if r[2] == "passed")
        failed = sum(1 for r in result_rows if r[2] in ("failed", "error"))
        started_at = run_row[6]
        ended_at = run_row[7]
        if started_at and ended_at:
            sa = started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None else started_at
            ea = ended_at.replace(tzinfo=timezone.utc) if ended_at.tzinfo is None else ended_at
            duration_ms = int((ea - sa).total_seconds() * 1000)
        else:
            duration_ms = sum(int(r[3] or 0) for r in result_rows)
        defect_count = sum(int(r[6] or 0) for r in result_rows)

        results = [
            ReportResultOut(
                id=r[0],
                test_name=r[1],
                status=r[2],
                duration_ms=int(r[3]) if r[3] is not None else None,
                error_message=r[4],
                created_at=r[5],
                defect_count=int(r[6] or 0),
                source_suite=r[7],
                tags=r[8],
            )
            for r in result_rows
        ]

        logger.info("Report detail fetched: %s by %s", run_id, current_user.id)
        return ReportDetailResponse(
            id=run_row[0],
            status=run_row[1],
            suite_name=run_row[2],
            suite_type=run_row[3],
            project_name=run_row[4],
            triggered_by_name=run_row[5],
            started_at=run_row[6],
            ended_at=run_row[7],
            created_at=run_row[8],
            total=total,
            passed=passed,
            failed=failed,
            duration_ms=duration_ms,
            defect_count=defect_count,
            results=results,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch report detail: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch report detail",
        )


@router.get("/{run_id}/export")
def export_report(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export full run report as a JSON file download."""
    from sqlalchemy import text

    try:
        # Fetch run
        run_row = db.execute(
            text(
                """
                SELECT tr.id, tr.status, ts.name AS suite_name, ts.suite_type,
                       p.name AS project_name, u.full_name AS triggered_by_name,
                       tr.started_at, tr.ended_at, tr.created_at
                FROM test_runs tr
                LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
                LEFT JOIN projects p ON ts.project_id = p.id
                LEFT JOIN users u ON tr.triggered_by = u.id
                WHERE tr.id = :run_id
                """
            ),
            {"run_id": str(run_id)},
        ).fetchone()

        if not run_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        # Fetch results
        result_rows = db.execute(
            text(
                """
                SELECT tr.id, tr.test_name, tr.status, tr.duration_ms,
                       tr.error_message, tr.stack_trace, tr.created_at,
                       tr.source_suite, tr.tags
                FROM test_results tr
                WHERE tr.test_run_id = :run_id
                ORDER BY tr.created_at
                """
            ),
            {"run_id": str(run_id)},
        ).fetchall()

        results = [
            {
                "id": str(r[0]),
                "test_name": r[1],
                "status": r[2],
                "duration_ms": int(r[3]) if r[3] is not None else None,
                "error_message": r[4],
                "stack_trace": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
                "source_suite": r[7],
                "tags": r[8],
            }
            for r in result_rows
        ]

        report_data = {
            "run_id": str(run_row[0]),
            "status": run_row[1],
            "suite_name": run_row[2],
            "suite_type": run_row[3],
            "project_name": run_row[4],
            "triggered_by": run_row[5],
            "started_at": run_row[6].isoformat() if run_row[6] else None,
            "ended_at": run_row[7].isoformat() if run_row[7] else None,
            "created_at": run_row[8].isoformat() if run_row[8] else None,
            "total_tests": len(results),
            "passed": sum(1 for r in results if r["status"] == "passed"),
            "failed": sum(1 for r in results if r["status"] in ("failed", "error")),
            "results": results,
        }

        logger.info("Report exported: %s by %s", run_id, current_user.id)
        return JSONResponse(
            content=report_data,
            headers={
                "Content-Disposition": f'attachment; filename="report_{run_id}.json"'
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to export report: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export report",
        )


AUTOMATION_ROOT = os.environ.get("AUTOMATION_ROOT", "/automation")


@router.get("/{run_id}/videos")
def list_videos(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List available video recordings for a test run's suite."""
    from sqlalchemy import text

    row = db.execute(
        text(
            """
            SELECT ts.name AS suite_name, p.name AS project_name
            FROM test_runs tr
            LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
            LEFT JOIN projects p ON ts.project_id = p.id
            WHERE tr.id = :run_id
            """
        ),
        {"run_id": str(run_id)},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Run not found")

    suite_name = row[0] or ""
    project_name = row[1] or ""

    project_dir = project_name.strip().lower().replace(" ", "_")
    videos_root = Path(AUTOMATION_ROOT) / project_dir / "test-artifacts" / "videos"

    if not videos_root.is_dir():
        return JSONResponse(content={"videos": [], "suite_folder": None})

    matched_folder = None
    suite_lower = suite_name.lower().replace(" ", "_")
    for folder in videos_root.iterdir():
        if folder.is_dir():
            folder_lower = folder.name.lower().replace(" ", "_")
            if suite_lower in folder_lower or folder_lower.startswith(suite_lower):
                matched_folder = folder
                break

    if not matched_folder:
        return JSONResponse(content={"videos": [], "suite_folder": None})

    videos = []
    for f in sorted(matched_folder.iterdir()):
        if f.is_file() and f.suffix.lower() in (".webm", ".mp4", ".avi", ".mkv"):
            videos.append({
                "filename": f.name,
                "test_name": f.stem.replace("_", " "),
                "size_bytes": f.stat().st_size,
            })

    return JSONResponse(content={
        "videos": videos,
        "suite_folder": matched_folder.name,
    })


@router.get("/{run_id}/videos/{filename}")
def serve_video(
    run_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Serve a video recording file for streaming/download."""
    from sqlalchemy import text

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    row = db.execute(
        text(
            """
            SELECT ts.name AS suite_name, p.name AS project_name
            FROM test_runs tr
            LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
            LEFT JOIN projects p ON ts.project_id = p.id
            WHERE tr.id = :run_id
            """
        ),
        {"run_id": str(run_id)},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Run not found")

    suite_name = row[0] or ""
    project_name = row[1] or ""

    project_dir = project_name.strip().lower().replace(" ", "_")
    videos_root = Path(AUTOMATION_ROOT) / project_dir / "test-artifacts" / "videos"

    suite_lower = suite_name.lower().replace(" ", "_")
    matched_folder = None
    if videos_root.is_dir():
        for folder in videos_root.iterdir():
            if folder.is_dir():
                folder_lower = folder.name.lower().replace(" ", "_")
                if suite_lower in folder_lower or folder_lower.startswith(suite_lower):
                    matched_folder = folder
                    break

    if not matched_folder:
        raise HTTPException(status_code=404, detail="Video folder not found")

    video_path = matched_folder / filename
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    media_type = "video/webm" if video_path.suffix == ".webm" else "video/mp4"
    return FileResponse(video_path, media_type=media_type, filename=filename)


def _find_suite_folder(base_dir: Path, suite_name: str):
    """Match a suite name to an artifacts subfolder."""
    if not base_dir.is_dir():
        return None
    suite_lower = suite_name.lower().replace(" ", "_")
    for folder in base_dir.iterdir():
        if folder.is_dir():
            folder_lower = folder.name.lower().replace(" ", "_")
            if suite_lower in folder_lower or folder_lower.startswith(suite_lower):
                return folder
    return None


def _get_run_suite_project(run_id: UUID, db: Session):
    """Return (suite_name, project_name) for a run."""
    from sqlalchemy import text

    row = db.execute(
        text(
            """
            SELECT ts.name AS suite_name, p.name AS project_name
            FROM test_runs tr
            LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
            LEFT JOIN projects p ON ts.project_id = p.id
            WHERE tr.id = :run_id
            """
        ),
        {"run_id": str(run_id)},
    ).fetchone()
    if not row:
        return None, None
    return row[0] or "", row[1] or ""


@router.get("/{run_id}/ai-suggestions")
def list_ai_suggestions(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List AI locator suggestions for failed tests in a run."""
    suite_name, project_name = _get_run_suite_project(run_id, db)
    if suite_name is None:
        raise HTTPException(status_code=404, detail="Run not found")

    project_dir = project_name.strip().lower().replace(" ", "_")
    suggestions_root = Path(AUTOMATION_ROOT) / project_dir / "test-artifacts" / "ai_suggestions"
    matched_folder = _find_suite_folder(suggestions_root, suite_name)

    if not matched_folder:
        return JSONResponse(content={"suggestions": [], "suite_folder": None})

    suggestions = []
    for f in sorted(matched_folder.iterdir()):
        if f.is_file() and f.suffix.lower() == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                suggestions.append({
                    "filename": f.name,
                    "test_name": data.get("test_name", f.stem.replace("_", " ")),
                    "failed_locator": data.get("failed_locator", ""),
                    "failure_message": data.get("failure_message_excerpt", ""),
                    "model": data.get("model", ""),
                    "status": data.get("status", "queued"),
                    "analysis": data.get("analysis", ""),
                    "suggestions": data.get("suggestions", []),
                    "timestamp": data.get("timestamp", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue

    return JSONResponse(content={
        "suggestions": suggestions,
        "suite_folder": matched_folder.name,
    })


@router.post("/{run_id}/ai-suggestions/{filename}/approve")
def approve_ai_suggestion(
    run_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark an AI suggestion as approved."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    suite_name, project_name = _get_run_suite_project(run_id, db)
    if suite_name is None:
        raise HTTPException(status_code=404, detail="Run not found")

    project_dir = project_name.strip().lower().replace(" ", "_")
    suggestions_root = Path(AUTOMATION_ROOT) / project_dir / "test-artifacts" / "ai_suggestions"
    matched_folder = _find_suite_folder(suggestions_root, suite_name)

    if not matched_folder:
        raise HTTPException(status_code=404, detail="Suggestions folder not found")

    file_path = matched_folder / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Suggestion file not found")

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        data["status"] = "approved"
        data["approved_by"] = str(current_user.id)
        data["approved_at"] = datetime.now(timezone.utc).isoformat()
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("AI suggestion approved: %s by %s", filename, current_user.id)
        return JSONResponse(content={"status": "approved", "filename": filename})
    except Exception as exc:
        logger.error("Failed to approve suggestion %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed to approve suggestion")
