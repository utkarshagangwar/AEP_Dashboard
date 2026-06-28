"""Test execution routes — trigger, query, cancel, and stream runs."""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, require_roles
from app.core.logging import get_logger
from app.models.test_result import TestResult, TestStatus
from app.models.test_run import RunStatus, TestRun
from app.models.test_suite import TestSuite
from app.models.user import User, UserRole
from app.schemas.run import (
    RunCreate,
    RunDetailResponse,
    RunListResponse,
    RunListItem,
    RunResponse,
    RunSummary,
    TestResultOut,
)
from app.services.audit_service import write_audit_log

logger = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

STALE_RUN_THRESHOLD_MINUTES = 10


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── POST /runs — trigger a new run ──────────────────────────────────────────
@router.post("", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
def trigger_run(
    payload: RunCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(UserRole.admin, UserRole.qa_lead, UserRole.qa_engineer)
    ),
):
    """Create a test run and enqueue it for execution."""
    try:
        # Verify suite exists and is active
        suite = db.get(TestSuite, payload.suite_id)
        if suite is None or not suite.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test suite not found or inactive",
            )

        # Check no queued/running run exists for this suite
        active = (
            db.query(TestRun)
            .filter(
                TestRun.test_suite_id == payload.suite_id,
                TestRun.status.in_([RunStatus.queued, RunStatus.running]),
            )
            .first()
        )
        if active is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A run is already queued or running for this suite",
            )

        run = TestRun(
            test_suite_id=payload.suite_id,
            status=RunStatus.queued,
            triggered_by=current_user.id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Enqueue Celery task
        from app.workers.tasks.execution import execute_test_suite

        task = execute_test_suite.delay(str(run.id))
        run.celery_task_id = task.id
        db.commit()
        db.refresh(run)

        write_audit_log(
            db,
            user_id=current_user.id,
            action="trigger_run",
            resource_type="test_run",
            resource_id=str(run.id),
            details={"suite_id": str(payload.suite_id)},
            ip_address=_client_ip(request),
        )

        logger.info("Run triggered: %s by %s (task=%s)", run.id, current_user.id, task.id)
        return run

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Trigger run DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── GET /runs — list runs ───────────────────────────────────────────────────
@router.get("", response_model=RunListResponse)
def list_runs(
    suite_id: UUID | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    run_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paginated test runs with optional filters."""
    try:
        offset = (page - 1) * limit
        where = "WHERE 1=1"
        params: dict = {}

        if suite_id:
            params["suite_id"] = str(suite_id)
            where += " AND tr.test_suite_id = :suite_id"
        if project_id:
            params["project_id"] = str(project_id)
            where += " AND ts.project_id = :project_id"
        if run_status:
            valid_statuses = [s.value for s in RunStatus]
            if run_status not in valid_statuses:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
                )
            params["run_status"] = run_status
            where += " AND tr.status = :run_status"

        count_row = db.execute(
            text(
                f"SELECT COUNT(*) FROM test_runs tr "
                f"LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id {where}"
            ),
            params,
        ).scalar()
        total = int(count_row or 0)

        params["limit_val"] = limit
        params["offset_val"] = offset
        rows = db.execute(
            text(
                f"SELECT tr.id, tr.test_suite_id, tr.status, tr.started_at, tr.ended_at, tr.created_at,"
                f"  ts.name AS suite_name, p.name AS project_name,"
                f"  u.full_name AS triggered_by_name,"
                f"  (SELECT COUNT(*) FROM test_results trs WHERE trs.test_run_id = tr.id) AS total,"
                f"  (SELECT COUNT(*) FROM test_results trs WHERE trs.test_run_id = tr.id AND trs.status = 'passed') AS passed,"
                f"  (SELECT COUNT(*) FROM test_results trs WHERE trs.test_run_id = tr.id AND trs.status = 'failed') AS failed"
                f" FROM test_runs tr"
                f" LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id"
                f" LEFT JOIN projects p ON ts.project_id = p.id"
                f" LEFT JOIN users u ON tr.triggered_by = u.id"
                f" {where}"
                f" ORDER BY tr.created_at DESC"
                f" LIMIT :limit_val OFFSET :offset_val"
            ),
            params,
        ).fetchall()

        items = [
            RunListItem(
                id=r.id,
                test_suite_id=r.test_suite_id,
                suite_name=r.suite_name,
                project_name=r.project_name,
                triggered_by_name=r.triggered_by_name,
                status=r.status.value if hasattr(r.status, "value") else r.status,
                started_at=r.started_at,
                ended_at=r.ended_at,
                created_at=r.created_at,
                total=int(r.total or 0),
                passed=int(r.passed or 0),
                failed=int(r.failed or 0),
            )
            for r in rows
        ]

        return RunListResponse(data=items, total=total, page=page, limit=limit)

    except SQLAlchemyError as exc:
        logger.error("List runs DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── GET /runs/{run_id} — run detail ─────────────────────────────────────────
@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a test run with all its test results and summary stats."""
    try:
        run = db.get(TestRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        if run.status in (RunStatus.running, RunStatus.queued) and run.started_at:
            started = run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at
            age = datetime.now(timezone.utc) - started
            if age > timedelta(minutes=STALE_RUN_THRESHOLD_MINUTES):
                _reconcile_run(run, db)
                db.refresh(run)

        # Joined info
        suite = db.get(TestSuite, run.test_suite_id)
        project_name = None
        suite_name = suite.name if suite else None
        if suite:
            from app.models.project import Project

            proj = db.get(Project, suite.project_id)
            project_name = proj.name if proj else None

        triggered_by_name = None
        if run.triggered_by:
            from app.models.user import User as UserModel

            trigger_user = db.get(UserModel, run.triggered_by)
            triggered_by_name = trigger_user.full_name if trigger_user else None

        # Results
        results = (
            db.query(TestResult)
            .filter(TestResult.test_run_id == run_id)
            .order_by(TestResult.created_at)
            .all()
        )

        total = len(results)
        passed = sum(1 for r in results if r.status.value == "passed")
        failed = sum(1 for r in results if r.status.value == "failed")
        duration = sum(r.duration_ms or 0 for r in results)

        return RunDetailResponse(
            id=run.id,
            test_suite_id=run.test_suite_id,
            status=run.status.value,
            triggered_by=run.triggered_by,
            started_at=run.started_at,
            ended_at=run.ended_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
            suite_name=suite_name,
            project_name=project_name,
            triggered_by_name=triggered_by_name,
            summary=RunSummary(total=total, passed=passed, failed=failed, duration_ms=duration),
            results=[TestResultOut.model_validate(r) for r in results],
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Get run DB error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── DELETE /runs/{run_id}/cancel ────────────────────────────────────────────
@router.delete("/{run_id}/cancel", status_code=status.HTTP_200_OK)
def cancel_run(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Cancel a queued or running test run and revoke the Celery task."""
    try:
        run = db.get(TestRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        if run.status not in (RunStatus.queued, RunStatus.running):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel run with status '{run.status.value}'",
            )

        # Revoke Celery task if present
        if run.celery_task_id:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(run.celery_task_id, terminate=True)

        run.status = RunStatus.cancelled
        run.ended_at = func.now()
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="cancel_run",
            resource_type="test_run",
            resource_id=str(run.id),
            ip_address=_client_ip(request),
        )

        logger.info("Run cancelled: %s by %s", run_id, current_user.id)
        return {"message": "Run cancelled successfully"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Cancel run DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── DELETE /runs/{run_id} ──────────────────────────────────────────────────
@router.delete("/{run_id}", status_code=status.HTTP_200_OK)
def delete_run(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Permanently delete a test run and its results."""
    try:
        run = db.get(TestRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        if run.status in (RunStatus.queued, RunStatus.running):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete an active run. Cancel it first.",
            )

        db.delete(run)
        db.commit()

        write_audit_log(
            db,
            user_id=current_user.id,
            action="delete_run",
            resource_type="test_run",
            resource_id=str(run_id),
            ip_address=_client_ip(request),
        )

        logger.info("Run deleted: %s by %s", run_id, current_user.id)
        return {"message": "Run deleted successfully"}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Delete run DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


def _reconcile_run(run: TestRun, db: Session) -> bool:
    """
    Attempt to recover a stale run by parsing its output.xml.

    Returns True if reconciliation succeeded and the run status was updated.
    """
    from app.workers.tasks.execution import _parse_robot_output_xml

    suite = db.get(TestSuite, run.test_suite_id)
    if suite is None:
        run.status = RunStatus.error
        run.ended_at = func.now()
        db.commit()
        return True

    automation_root = Path(settings.AUTOMATION_ROOT) if settings.AUTOMATION_ROOT else None
    if not automation_root:
        return False

    output_xml = None
    for project_dir in automation_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        candidate = project_dir / "results" / str(run.id) / "output.xml"
        if candidate.exists():
            output_xml = candidate
            break

    if output_xml is None:
        run.status = RunStatus.error
        run.ended_at = func.now()
        db.commit()
        logger.info("Stale run %s marked as error (no output.xml found)", run.id)
        return True

    test_results = _parse_robot_output_xml(str(output_xml))

    existing_count = (
        db.query(TestResult).filter(TestResult.test_run_id == run.id).count()
    )
    if existing_count == 0:
        for test_name, test_status, duration_ms, error_msg in test_results:
            db.add(TestResult(
                test_run_id=run.id,
                test_name=test_name,
                status=TestStatus(test_status),
                duration_ms=duration_ms,
                error_message=error_msg,
            ))
        db.flush()

    total = len(test_results) if existing_count == 0 else existing_count
    passed_count = (
        sum(1 for _, s, _, _ in test_results if s == "passed")
        if existing_count == 0
        else db.query(TestResult)
        .filter(TestResult.test_run_id == run.id, TestResult.status == TestStatus.passed)
        .count()
    )
    failed_count = total - passed_count

    if total == 0:
        run.status = RunStatus.error
    else:
        run.status = RunStatus.passed if failed_count == 0 else RunStatus.failed
    run.ended_at = func.now()
    db.commit()

    logger.info(
        "Stale run %s reconciled: status=%s total=%d passed=%d failed=%d",
        run.id, run.status.value, total, passed_count, failed_count,
    )
    return True


# ── POST /runs/{run_id}/reconcile — recover stuck run ─────────────────────
@router.post("/{run_id}/reconcile", status_code=status.HTTP_200_OK)
def reconcile_run(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.qa_lead)),
):
    """Manually reconcile a stuck run by re-parsing its output.xml."""
    try:
        run = db.get(TestRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found",
            )

        if run.status not in (RunStatus.queued, RunStatus.running):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run is already in terminal state '{run.status.value}'",
            )

        success = _reconcile_run(run, db)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not reconcile run — automation root not configured",
            )

        write_audit_log(
            db,
            user_id=current_user.id,
            action="reconcile_run",
            resource_type="test_run",
            resource_id=str(run.id),
            details={"new_status": run.status.value},
            ip_address=_client_ip(request),
        )

        return {"message": "Run reconciled", "status": run.status.value}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Reconcile run DB error: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ── GET /runs/{run_id}/stream — SSE ─────────────────────────────────────────
@router.get("/{run_id}/stream")
async def stream_run(
    run_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Server-Sent Events stream that pushes run status and results every second."""

    async def event_generator():
        from app.core.database import SessionLocal

        terminal = {"passed", "failed", "error", "cancelled"}
        while True:
            session: Session = SessionLocal()
            try:
                run = session.get(TestRun, run_id)
                if run is None:
                    yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                    return

                status_val = run.status.value

                if status_val in ("running", "queued") and run.started_at:
                    age = datetime.now(timezone.utc) - run.started_at.replace(
                        tzinfo=timezone.utc
                    ) if run.started_at.tzinfo is None else datetime.now(timezone.utc) - run.started_at
                    if age > timedelta(minutes=STALE_RUN_THRESHOLD_MINUTES):
                        logger.warning("Stream detected stale run %s (age=%s), reconciling", run_id, age)
                        _reconcile_run(run, session)
                        session.refresh(run)
                        status_val = run.status.value

                results = (
                    session.query(TestResult)
                    .filter(TestResult.test_run_id == run_id)
                    .all()
                )
                completed = len(results)
                passed = sum(1 for r in results if r.status.value == "passed")
                failed = sum(1 for r in results if r.status.value == "failed")
                total_tests = run.total_tests or completed

                payload = {
                    "status": status_val,
                    "total": total_tests,
                    "completed": completed,
                    "passed": passed,
                    "failed": failed,
                    "results": [
                        {
                            "id": str(r.id),
                            "test_name": r.test_name,
                            "status": r.status.value,
                            "duration_ms": r.duration_ms,
                            "error_message": r.error_message,
                        }
                        for r in results
                    ],
                }
                yield f"data: {json.dumps(payload)}\n\n"

                if status_val in terminal:
                    return
            finally:
                session.close()
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
