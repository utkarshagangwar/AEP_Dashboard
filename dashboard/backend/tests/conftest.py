"""Shared pytest fixtures for the unit test suite.

These tests must run with no database, no network, no API keys and no
browser. Anything requiring those belongs in golden_tests/ (the manual
harness) or the Robot Framework suites.

KNOWN COUPLING (flagged, not fixed here -- outside SOW_CHUNKING_PLAN scope):
importing any app.services.* module executes app/services/__init__.py, which
eagerly imports audit_service/auth_service/user_service and therefore pulls
in SQLAlchemy and constructs the engine in app.core.database at import time.
A pure text-extraction function should not require a DB driver to be
importable. It works because requirements.txt installs psycopg2, but it
makes this suite slower and more fragile than it needs to be. Fixing it
means making app/services/__init__.py lazy, which touches every service
consumer -- a separate change.
"""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

FIXTURE_DIR = os.path.join(BACKEND_ROOT, "tests", "fixtures", "generated")


@pytest.fixture(scope="session", autouse=True)
def generated_fixtures():
    """Build the binary fixtures once per session if they are absent.

    Generated rather than committed so their structure stays reviewable in
    make_fixtures.py. Rebuilt automatically so a fresh clone needs no manual
    step before `pytest` works.
    """
    from tests.fixtures import make_fixtures

    missing = [
        name for name in make_fixtures.BUILDERS
        if not os.path.exists(os.path.join(FIXTURE_DIR, name))
    ]
    if missing:
        make_fixtures.build_all()
    return FIXTURE_DIR


@pytest.fixture
def fixture_path(generated_fixtures):
    """fixture_path("structured.docx") -> absolute path."""
    def _resolve(name: str) -> str:
        path = os.path.join(generated_fixtures, name)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Fixture {name!r} missing. Run: python3 tests/fixtures/make_fixtures.py"
            )
        return path

    return _resolve
