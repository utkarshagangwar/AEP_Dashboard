"""End-to-end regression tests for the blank-page failure, at the exact
layer that caused it: _resolve_run_inputs in the Celery task.

tests/test_start_context.py covers the resolver in isolation. This file
covers the thing the resolver feeds — the five-tuple handed to
ai_runner — because that is where the symptom was actually produced:

    ai_runner._execute_steps:
        if environment_url and environment_url != "about:blank":
            await page.goto(...)

An environment_url of "about:blank" means page.goto() is never called at
all. The agent then opens a blank tab and reports "the browser
encountered a blank page", which reads like a page-load problem but is
really "no navigation was ever attempted".

Every assertion here is therefore ultimately about one question: does
environment_url come back as something page.goto() will actually
navigate to?
"""
from __future__ import annotations

import uuid

import pytest

from app.models.ai_runs import AICredentialProfile, AITestRun
from app.models.project import Project, ProjectEnvironment
from app.workers.tasks.ai_execution import _resolve_run_inputs

NO_NAV = "about:blank"


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, *, projects=None, profiles=None, environments=None):
        self._projects = {p.id: p for p in (projects or [])}
        self._profiles = {p.id: p for p in (profiles or [])}
        self._environments = list(environments or [])

    def get(self, model, pk):
        if model is Project:
            return self._projects.get(pk)
        if model is AICredentialProfile:
            return self._profiles.get(pk)
        raise AssertionError(f"unexpected db.get for {model}")

    def query(self, model):
        assert model is ProjectEnvironment
        return _FakeQuery(self._environments)


def _run(**kwargs):
    kwargs.setdefault("id", uuid.uuid4())
    kwargs.setdefault("goal", 'Click the "Add Job" button in the Jobs module')
    return AITestRun(**kwargs)


def test_sow_skill_run_no_longer_lands_on_about_blank():
    """The reported failure, reproduced and fixed.

    A skill extracted from a SOW: assigned to a project, no credential
    profile, no ad-hoc URL, and an instruction naming no address.
    """
    proj = Project(id=uuid.uuid4(), name="Vikaas")
    db = _FakeDB(
        projects=[proj],
        environments=[
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="staging",
                base_url="https://app.test/dashboard",
            )
        ],
    )
    run = _run(project_id=proj.id, environment="staging")

    url, allowed, sensitive, cookies, unrestricted = _resolve_run_inputs(db, run)

    # The assertion that matters: page.goto() will now be called.
    assert url != NO_NAV
    assert url == "https://app.test/dashboard"
    assert sensitive is None  # no default login configured
    assert cookies is None
    assert unrestricted is False


def test_project_default_profile_supplies_credentials_and_an_allowlist():
    """A project-default standard profile with no allowed_domains of its
    own must not be rejected by ai_runner's sensitive_data gate — the
    allowlist is derived from the target host instead, which is strictly
    tighter than running unrestricted."""
    from app.services.credential_service import encrypt_credentials

    prof = AICredentialProfile(
        id=uuid.uuid4(),
        name="QA user",
        kind="standard",
        target_url=None,
        allowed_domains=None,
        credentials_json=encrypt_credentials({"username": "qa", "password": "pw"}),
    )
    proj = Project(
        id=uuid.uuid4(), name="Vikaas", default_credential_profile_id=prof.id
    )
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="staging",
                base_url="https://app.test/dashboard",
            )
        ],
    )
    run = _run(project_id=proj.id, environment="staging")

    url, allowed, sensitive, cookies, unrestricted = _resolve_run_inputs(db, run)

    assert url == "https://app.test/dashboard"
    assert sensitive == {"username": "qa", "password": "pw"}
    # Gate satisfied honestly rather than switched off.
    assert allowed and "app.test" in allowed
    assert unrestricted is False


def test_standard_profile_target_url_is_used_for_navigation():
    """Previously target_url was read only in the bypass branch, so this
    run resolved to about:blank despite having a fully configured
    profile."""
    proj = Project(id=uuid.uuid4(), name="Vikaas")
    prof = AICredentialProfile(
        id=uuid.uuid4(),
        name="QA user",
        kind="standard",
        target_url="https://app.test/login",
        allowed_domains=["app.test"],
        credentials_json=None,
    )
    db = _FakeDB(projects=[proj], profiles=[prof])
    run = _run(project_id=proj.id, credential_profile_id=prof.id)

    url, allowed, _sensitive, _cookies, _unrestricted = _resolve_run_inputs(db, run)

    assert url == "https://app.test/login"
    assert "app.test" in allowed


def test_adhoc_run_is_unchanged_by_the_project_fallback():
    """Ad-hoc runs deliberately opt out of domain allowlisting. They must
    not silently acquire a project's saved credentials or allowlist."""
    from app.services.credential_service import encrypt_credentials

    proj = Project(id=uuid.uuid4(), name="Vikaas")
    other = AICredentialProfile(
        id=uuid.uuid4(),
        name="Project default",
        kind="standard",
        allowed_domains=["project.test"],
        credentials_json=encrypt_credentials({"username": "p", "password": "p"}),
    )
    db = _FakeDB(
        projects=[proj],
        profiles=[other],
        environments=[
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="staging",
                base_url="https://project.test/",
                default_credential_profile_id=other.id,
            )
        ],
    )
    run = _run(
        project_id=proj.id,
        environment="staging",
        adhoc_target_url="https://adhoc.test/",
        adhoc_credentials_json=encrypt_credentials(
            {"username": "a", "password": "a"}
        ),
    )

    url, allowed, sensitive, cookies, unrestricted = _resolve_run_inputs(db, run)

    assert url == "https://adhoc.test/"
    assert sensitive == {"username": "a", "password": "a"}
    assert unrestricted is True
    # The project's allowlist must not leak into an unrestricted run.
    assert allowed is None
    assert cookies is None


def test_unconfigured_project_still_degrades_to_about_blank():
    """The worker never raises for missing configuration — the API's
    fail-fast gate is what rejects these. A run that reaches the worker
    unconfigured (e.g. an environment deleted between submit and
    execution) must degrade exactly as before, not crash the task."""
    proj = Project(id=uuid.uuid4(), name="Vikaas")
    db = _FakeDB(projects=[proj])
    run = _run(project_id=proj.id, environment="staging")

    url, allowed, sensitive, cookies, unrestricted = _resolve_run_inputs(db, run)

    assert url == NO_NAV
    assert sensitive is None
    assert cookies is None
    assert unrestricted is False


# ── Bypass cookie injection (the reported regression) ───────────────────────
#
# ai_runner._execute_steps only injects cookies when `cookies` is
# truthy, and emits "Inject authenticated session cookie" as its own
# visible step when it does. In the reported failure that step was
# absent from the action log entirely, which proved cookies had never
# been resolved: the run started unauthenticated, was bounced to the
# target app's real login form, and hit its reCAPTCHA.
#
# These tests assert on `cookies` because that single value decides
# whether the agent arrives logged in or fights a login form.


def _bypass_profile(monkeypatch, *, target_url="https://app.test/jobs"):
    """A kind="bypass" profile with its admin-token HTTP call stubbed.

    _resolve_bypass_profile makes a real requests.get() to the target
    app's admin endpoint. Stubbing it keeps this suite offline (per
    tests/conftest.py) while still exercising the full resolution path.
    """
    from app.services.credential_service import encrypt_credentials

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"token": "tok-123"}

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())

    return AICredentialProfile(
        id=uuid.uuid4(),
        name="IG Login Bypass",
        kind="bypass",
        target_url=target_url,
        allowed_domains=["app.test"],
        credentials_json=encrypt_credentials(
            {
                "api_base_url": "https://api.app.test",
                "bypass_endpoint": "/auth/admin-token",
                "api_key": "k",
                "cookie_name": "authToken",
                "cookie_domain": "app.test",
            }
        ),
    )


def test_project_default_bypass_profile_injects_cookies(monkeypatch):
    """A SOW skill with no login of its own must pick up the project's
    bypass profile and arrive already authenticated."""
    prof = _bypass_profile(monkeypatch)
    proj = Project(
        id=uuid.uuid4(), name="IG Automation", default_credential_profile_id=prof.id
    )
    db = _FakeDB(projects=[proj], profiles=[prof])
    run = _run(project_id=proj.id, environment=None)

    url, allowed, sensitive, cookies, _unrestricted = _resolve_run_inputs(db, run)

    assert cookies, "bypass cookies must be resolved — this is the reported bug"
    assert cookies[0]["name"] == "authToken"
    assert cookies[0]["value"] == "tok-123"
    assert url == "https://app.test/jobs"


def test_goal_embedded_url_does_not_suppress_bypass_cookies(monkeypatch):
    """The precise reported failure.

    The SOW goal named dev.interviewgod.ai, which satisfied the URL
    requirement, so login resolution was skipped and no cookie was ever
    injected. A goal carrying its own address must waive only the URL
    requirement, never the login.
    """
    prof = _bypass_profile(monkeypatch)
    proj = Project(
        id=uuid.uuid4(), name="IG Automation", default_credential_profile_id=prof.id
    )
    db = _FakeDB(projects=[proj], profiles=[prof])
    run = _run(
        project_id=proj.id,
        environment=None,
        goal="Navigate to https://app.test/jobs and click the Add Job button",
    )

    _url, _allowed, _sensitive, cookies, _unrestricted = _resolve_run_inputs(db, run)

    assert cookies, "a goal-embedded URL must not suppress the bypass login"


def test_unmatched_environment_label_does_not_lose_the_bypass_login(monkeypatch):
    """A SOW skill has environment = NULL. Even when the project has
    environment rows that cannot be matched, the login must survive."""
    prof = _bypass_profile(monkeypatch)
    proj = Project(
        id=uuid.uuid4(), name="IG Automation", default_credential_profile_id=prof.id
    )
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="production",
                base_url="https://prod.test/",
            ),
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="dev",
                base_url="https://dev.test/",
            ),
        ],
    )
    run = _run(project_id=proj.id, environment=None)

    url, _allowed, _sensitive, cookies, _unrestricted = _resolve_run_inputs(db, run)

    assert cookies, "an ambiguous environment must not cost the run its login"
    # The cookie is only valid for the bypass profile's own host, so that
    # address must win over either ambiguous environment row.
    assert url == "https://app.test/jobs"


def test_explicit_bypass_profile_still_works_unchanged(monkeypatch):
    """The New Test path, which was always working. Must stay identical."""
    prof = _bypass_profile(monkeypatch)
    proj = Project(id=uuid.uuid4(), name="IG Automation")
    db = _FakeDB(projects=[proj], profiles=[prof])
    run = _run(project_id=proj.id, credential_profile_id=prof.id)

    url, allowed, _sensitive, cookies, _unrestricted = _resolve_run_inputs(db, run)

    assert cookies and cookies[0]["value"] == "tok-123"
    assert url == "https://app.test/jobs"
    assert "app.test" in allowed


def test_broken_bypass_profile_fails_the_run_instead_of_running_logged_out(monkeypatch):
    """A project-default bypass that cannot fetch its token must abort the
    run, exactly as an explicitly-chosen bypass profile already does.

    This test previously asserted the opposite — that the run continued
    with cookies=None — and that "graceful" degradation is what produced
    the failure this replaces: with the admin-token endpoint returning
    404, a Skills run started logged out, the agent typed a work email
    into the real login form, exhausted its steps, and the engineer was
    handed a verdict about the feature under test instead of "bypass
    login failed"."""
    import requests

    def _boom(*_a, **_k):
        raise RuntimeError("admin endpoint unreachable")

    monkeypatch.setattr(requests, "get", _boom)

    from app.services.credential_service import encrypt_credentials

    prof = AICredentialProfile(
        id=uuid.uuid4(),
        name="Broken Bypass",
        kind="bypass",
        target_url="https://app.test/jobs",
        allowed_domains=["app.test"],
        credentials_json=encrypt_credentials(
            {
                "api_base_url": "https://api.app.test",
                "api_key": "k",
                "cookie_domain": "app.test",
            }
        ),
    )
    proj = Project(
        id=uuid.uuid4(), name="IG Automation", default_credential_profile_id=prof.id
    )
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[
            ProjectEnvironment(
                id=uuid.uuid4(),
                project_id=proj.id,
                environment="dev",
                base_url="https://dev.test/",
            )
        ],
    )
    run = _run(project_id=proj.id, environment="dev")

    with pytest.raises(RuntimeError) as excinfo:
        _resolve_run_inputs(db, run)

    # The message must name the profile and say no test result was
    # produced — the point is that the engineer reads a setup failure,
    # not a test verdict.
    assert "Broken Bypass" in str(excinfo.value)
    assert "admin endpoint unreachable" in str(excinfo.value)
