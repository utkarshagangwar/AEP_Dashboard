"""Celery task: execute real robot test suites from automation projects."""
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    future=True,
)
_SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False, future=True)

# Matches Robot Framework console output lines like:
#   Test Name                                                         | PASS |
#   Test Name ........................................................ | FAIL |
_RF_RESULT_RE = re.compile(r'^(.+?)\s*[.\s]+\| (PASS|FAIL) \|\s*$')
# Also match without dot padding:
_RF_RESULT_RE2 = re.compile(r'^(.+?)\s+\| (PASS|FAIL) \|\s*$')
# Suite summary stats line:
_RF_STATS_RE = re.compile(r'^\d+ tests?,')


def _fresh_session() -> Session:
    return _SessionFactory()


def _now():
    return datetime.now(timezone.utc)


def _count_robot_tests(robot_file: Path) -> int:
    """Count the number of test cases in a .robot file."""
    try:
        in_test_cases = False
        count = 0
        for line in robot_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("*** test cases"):
                in_test_cases = True
                continue
            if stripped.startswith("***"):
                in_test_cases = False
                continue
            if in_test_cases and stripped and not line[0].isspace():
                count += 1
        return count
    except Exception as exc:
        logger.warning("Failed to count tests in %s: %s", robot_file, exc)
        return 0


def _find_robot_suite_path(suite_name: str) -> Path | None:
    """Scan automation folder for a matching test suite."""
    automation_root = Path(settings.AUTOMATION_ROOT) if settings.AUTOMATION_ROOT else None

    if not automation_root or not automation_root.exists():
        logger.error("Automation root not found: %s", automation_root)
        return None

    for project_dir in automation_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        tests_dir = project_dir / "tests"
        if not tests_dir.exists():
            continue

        suite_dir = tests_dir / suite_name
        if not suite_dir.is_dir():
            continue

        for pattern in [f"{suite_name}_tests.robot", f"{suite_name}_test.robot"]:
            candidate = suite_dir / pattern
            if candidate.exists():
                return candidate

    logger.warning("Robot suite not found: %s", suite_name)
    return None


def _parse_robot_output_xml(output_xml_path: str) -> list[dict]:
    """Parse robot output.xml and extract test results with suite/tag metadata."""
    try:
        tree = ET.parse(output_xml_path)
        root = tree.getroot()

        results = []

        def _walk_suites(suite_elem, parent_suite_name=None):
            suite_name = suite_elem.get("name", parent_suite_name)
            for test in suite_elem.findall("test"):
                test_name = test.get("name", "unknown")

                status_elem = test.find("status")
                if status_elem is None:
                    continue

                raw_status = status_elem.get("status", "").upper()
                if raw_status == "PASS":
                    status = "passed"
                elif raw_status == "FAIL":
                    status = "failed"
                else:
                    continue

                elapsed_ms = 0
                if status_elem.get("elapsed"):
                    try:
                        elapsed_ms = int(float(status_elem.get("elapsed")) * 1000)
                    except (ValueError, TypeError):
                        pass
                elif status_elem.get("elapsedtime"):
                    try:
                        elapsed_ms = int(status_elem.get("elapsedtime"))
                    except (ValueError, TypeError):
                        pass

                error_msg = None
                if status == "failed":
                    msg_elem = test.find(".//msg[@level='FAIL']")
                    if msg_elem is not None and msg_elem.text:
                        error_msg = msg_elem.text[:2000]

                tag_elems = test.findall("tag")
                tags_str = ", ".join(t.text for t in tag_elems if t.text) or None

                results.append({
                    "test_name": test_name,
                    "status": status,
                    "duration_ms": elapsed_ms,
                    "error_message": error_msg,
                    "source_suite": suite_name,
                    "tags": tags_str,
                })

            for child_suite in suite_elem.findall("suite"):
                _walk_suites(child_suite, suite_name)

        for top_suite in root.findall("suite"):
            _walk_suites(top_suite)

        return results

    except Exception as exc:
        logger.error("Failed to parse output.xml: %s", exc)
        return []


def _insert_live_result(run_id, test_name: str, status: str) -> bool:
    """Insert a single test result using a fresh DB session. Returns True on success."""
    from app.models.test_result import TestResult, TestStatus

    session = _fresh_session()
    try:
        session.add(TestResult(
            test_run_id=run_id,
            test_name=test_name,
            status=TestStatus(status),
        ))
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        logger.warning("Failed to insert live result '%s': %s", test_name, exc)
        return False
    finally:
        session.close()


@shared_task(name="workers.tasks.execution.execute_test_suite", bind=True, max_retries=0)
def execute_test_suite(self, run_id: str):
    """Execute a real robot test suite and persist results."""
    from app.models.test_run import TestRun, RunStatus
    from app.models.test_suite import TestSuite
    from app.models.test_result import TestResult, TestStatus
    from app.models.audit_log import AuditLog

    # ── Phase 1: Setup (short-lived session) ────────────────────────────
    session = _fresh_session()
    try:
        run = session.get(TestRun, run_id)
        if run is None:
            session.close()
            return {"error": f"TestRun {run_id} not found"}

        suite = session.get(TestSuite, run.test_suite_id)
        if suite is None:
            session.close()
            return {"error": f"TestSuite {run.test_suite_id} not found"}

        suite_name = suite.name
        suite_id = suite.id
        triggered_by = run.triggered_by
        run_uuid = run.id

        run.status = RunStatus.running
        run.started_at = _now()
        run.celery_task_id = self.request.id
        session.commit()
    except Exception as exc:
        logger.error("Setup failed for run %s: %s", run_id, exc)
        session.rollback()
        session.close()
        return {"error": str(exc)}
    finally:
        session.close()  # Release DB connection before long subprocess

    # ── Phase 2: Find suite and build command ───────────────────────────
    try:
        robot_suite_path = _find_robot_suite_path(suite_name)
        if robot_suite_path is None:
            raise Exception(f"Robot test file not found for suite: {suite_name}")

        total_tests = _count_robot_tests(robot_suite_path)
        if total_tests > 0:
            s = _fresh_session()
            try:
                r = s.get(TestRun, run_id)
                if r:
                    r.total_tests = total_tests
                    s.commit()
            except Exception:
                s.rollback()
            finally:
                s.close()

        project_root = robot_suite_path.parent.parent.parent
        results_dir = project_root / "results" / run_id
        results_dir.mkdir(parents=True, exist_ok=True)

        listener_path = str(Path(__file__).parent / "rf_listener.py")

        cmd = [
            "robot",
            "--outputdir", str(results_dir),
            "--pythonpath", str(project_root),
            "--pythonpath", str(project_root / "libs"),
            "--listener", f"{listener_path}:{run_id}",
            "--variable", "BROWSER:headlesschromium",
            "--loglevel", "DEBUG",
            str(robot_suite_path),
        ]

        logger.info("Executing robot suite: %s (run_id=%s)", suite_name, run_id)
        logger.info("Command: %s", " ".join(cmd))

        env = {
            **os.environ,
            "BROWSER": "headlesschromium",
            "AEP_DATABASE_URL": settings.DATABASE_URL,
        }

        # ── Phase 3: Execute with live stdout parsing ───────────────────
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # Read stderr in background thread to prevent pipe deadlock
        stderr_lines = []
        def _read_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        # Parse Robot stdout line by line for live results
        inserted_tests = set()
        pending_result = None  # (test_name, status) — buffered to skip suite summaries

        for line in proc.stdout:
            stripped = line.rstrip("\n").rstrip()

            # If we have a pending result, check if current line is a suite stats line
            if pending_result:
                if _RF_STATS_RE.match(stripped.strip()):
                    # Previous line was a suite summary, discard it
                    pending_result = None
                else:
                    # Previous line was a real test result — insert it
                    tname, tstatus = pending_result
                    if tname not in inserted_tests:
                        if _insert_live_result(run_uuid, tname, tstatus):
                            inserted_tests.add(tname)
                            logger.info("Live result: %s → %s (%d inserted)", tname, tstatus, len(inserted_tests))
                    pending_result = None

            # Check if this line is a test/suite result
            m = _RF_RESULT_RE.match(stripped) or _RF_RESULT_RE2.match(stripped)
            if m:
                name = m.group(1).strip().rstrip(".")
                status = "passed" if m.group(2) == "PASS" else "failed"
                pending_result = (name, status)

        # Flush last pending result
        if pending_result:
            tname, tstatus = pending_result
            if tname not in inserted_tests:
                if _insert_live_result(run_uuid, tname, tstatus):
                    inserted_tests.add(tname)

        proc.wait(timeout=60)
        stderr_thread.join(timeout=5)

        stderr_text = "".join(stderr_lines)
        returncode = proc.returncode

        logger.info("Robot exit code: %d (live-inserted: %d tests)", returncode, len(inserted_tests))
        if stderr_text:
            logger.info("Robot stderr (last 1000): %s", stderr_text[-1000:])

    except Exception as exc:
        logger.error("Execution failed for run %s: %s", run_id, exc)
        # Mark run as error with a fresh session
        err_session = _fresh_session()
        try:
            run = err_session.get(TestRun, run_id)
            if run is not None:
                run.status = RunStatus.error
                run.ended_at = _now()
                err_session.commit()
        except Exception:
            err_session.rollback()
        finally:
            err_session.close()
        return {"error": str(exc)}

    # ── Phase 4: Finalize with fresh session ────────────────────────────
    session = _fresh_session()
    try:
        run = session.get(TestRun, run_id)
        if run is None:
            session.close()
            return {"error": f"TestRun {run_id} disappeared"}

        # Count results already in DB (from live parsing and/or listener)
        existing = (
            session.query(TestResult)
            .filter(TestResult.test_run_id == run_uuid)
            .all()
        )

        if len(existing) == 0:
            # Fallback: parse output.xml if no live results were inserted
            logger.info("No live results found, falling back to output.xml for run %s", run_id)
            output_xml = None
            for f in results_dir.iterdir():
                if f.name.startswith("output") and f.suffix == ".xml":
                    output_xml = f
                    break

            test_results = []
            if output_xml and output_xml.exists():
                test_results = _parse_robot_output_xml(str(output_xml))

            for tr_data in test_results:
                session.add(TestResult(
                    test_run_id=run_uuid,
                    test_name=tr_data["test_name"],
                    status=TestStatus(tr_data["status"]),
                    duration_ms=tr_data["duration_ms"],
                    error_message=tr_data["error_message"],
                    source_suite=tr_data.get("source_suite"),
                    tags=tr_data.get("tags"),
                ))
            session.flush()
            total = len(test_results)
            passed_count = sum(1 for t in test_results if t["status"] == "passed")
            failed_count = total - passed_count
        else:
            # Update results with duration/error from output.xml
            output_xml = None
            for f in results_dir.iterdir():
                if f.name.startswith("output") and f.suffix == ".xml":
                    output_xml = f
                    break

            if output_xml and output_xml.exists():
                xml_results = _parse_robot_output_xml(str(output_xml))
                xml_map = {d["test_name"]: d for d in xml_results}
                for r in existing:
                    if r.test_name in xml_map:
                        d = xml_map[r.test_name]
                        if d["duration_ms"] and not r.duration_ms:
                            r.duration_ms = d["duration_ms"]
                        if d["error_message"] and not r.error_message:
                            r.error_message = d["error_message"]
                        if d.get("source_suite") and not r.source_suite:
                            r.source_suite = d["source_suite"]
                        if d.get("tags") and not r.tags:
                            r.tags = d["tags"]
                session.flush()

            total = len(existing)
            passed_count = sum(1 for r in existing if r.status.value == "passed")
            failed_count = total - passed_count

        # Set final run status
        if total == 0:
            run.status = RunStatus.error
        else:
            run.status = RunStatus.passed if failed_count == 0 else RunStatus.failed
        run.ended_at = _now()
        session.commit()

        # Audit log
        try:
            session.add(AuditLog(
                user_id=triggered_by,
                action="test_run_completed",
                resource_type="test_run",
                resource_id=str(run_uuid),
                details={
                    "status": run.status.value,
                    "total": total,
                    "passed": passed_count,
                    "failed": failed_count,
                    "suite_name": suite_name,
                },
            ))
            session.commit()
        except Exception:
            session.rollback()

        logger.info(
            "Run completed: %s (total=%d, passed=%d, failed=%d)",
            run_id, total, passed_count, failed_count,
        )

        return {
            "run_id": str(run_uuid),
            "status": run.status.value,
            "total": total,
            "passed": passed_count,
            "failed": failed_count,
        }

    except Exception as exc:
        logger.error("Finalization failed for run %s: %s", run_id, exc)
        session.rollback()

        # Last-resort: try with yet another fresh session
        try:
            err_session = _fresh_session()
            run = err_session.get(TestRun, run_id)
            if run and run.status.value in ("running", "queued"):
                run.status = RunStatus.error
                run.ended_at = _now()
                err_session.commit()
            err_session.close()
        except Exception:
            pass

        return {"error": str(exc)}
    finally:
        session.close()
