"""Service to discover and register robot test suites from automation projects."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.project import Project
from app.models.test_suite import TestSuite, SuiteType

logger = get_logger(__name__)


def _infer_suite_type(suite_name: str) -> SuiteType | None:
    name_lower = suite_name.lower()
    if "smoke" in name_lower:
        return SuiteType.smoke
    if "regression" in name_lower:
        return SuiteType.regression
    if "sanity" in name_lower:
        return SuiteType.sanity
    return None


def discover_and_register_suites(db: Session) -> dict:
    """
    Scan the automation folder and register discovered robot test suites.

    Returns dict with discovered, registered, and errors lists.
    """
    discovered = []
    registered = []
    errors = []

    automation_root = Path(settings.AUTOMATION_ROOT) if settings.AUTOMATION_ROOT else None

    if not automation_root or not automation_root.exists():
        return {
            "discovered": [],
            "registered": [],
            "errors": [f"Automation root not found: {automation_root}"],
        }

    for project_dir in automation_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        project_name = project_dir.name
        tests_dir = project_dir / "tests"

        if not tests_dir.exists():
            continue

        for suite_dir in tests_dir.iterdir():
            if not suite_dir.is_dir() or suite_dir.name.startswith("_"):
                continue

            suite_name = suite_dir.name

            # Check for either naming convention
            robot_file = None
            for pattern in [f"{suite_name}_tests.robot", f"{suite_name}_test.robot"]:
                candidate = suite_dir / pattern
                if candidate.exists():
                    robot_file = candidate
                    break

            if robot_file is None:
                continue

            discovered.append({
                "project": project_name,
                "suite": suite_name,
                "path": str(robot_file),
            })

    try:
        for item in discovered:
            project_name = item["project"]
            suite_name = item["suite"]

            project = (
                db.query(Project)
                .filter(Project.name == project_name, Project.is_active.is_(True))
                .first()
            )
            if project is None:
                project = Project(name=project_name, is_active=True)
                db.add(project)
                db.flush()

            existing_suite = (
                db.query(TestSuite)
                .filter(
                    TestSuite.project_id == project.id,
                    TestSuite.name == suite_name,
                    TestSuite.is_active.is_(True),
                )
                .first()
            )

            if existing_suite is None:
                suite = TestSuite(
                    project_id=project.id,
                    name=suite_name,
                    suite_type=_infer_suite_type(suite_name),
                    is_active=True,
                )
                db.add(suite)
                registered.append(f"{project_name}/{suite_name}")

        db.commit()

    except Exception as exc:
        db.rollback()
        errors.append(f"Registration error: {str(exc)}")

    logger.info(
        "Suite discovery: %d discovered, %d registered",
        len(discovered), len(registered),
    )

    return {
        "discovered": discovered,
        "registered": registered,
        "errors": errors,
    }
