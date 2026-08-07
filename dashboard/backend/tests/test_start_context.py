"""Regression tests for app.services.start_context.

These lock down the exact defect that caused SOW-exported skills to fail
with "the browser encountered a blank page":

  A prompt skill extracted from a SOW is saved under a project but has no
  credential profile (parse time knows nothing about environments or
  logins) and its instruction text names no URL. That resolved to
  environment_url="about:blank"; ai_runner's `!= "about:blank"` guard then
  skipped page.goto() entirely, so the agent opened a blank tab and
  reported a blank page before any functional step could run.

Equally important, they pin the paths that ALREADY worked — bypass
profiles, ad-hoc target URLs, goals with an embedded address — so the new
project-environment fallback cannot silently change where an existing run
navigates.

No database: the resolver only ever calls db.get() and a single
db.query(...).filter(...).all(), so a small fake session is both
sufficient and faster than a real one, per tests/conftest.py's
"no DB, no network, no browser" rule for this suite.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.ai_runs import AICredentialProfile
from app.models.project import Project, ProjectEnvironment
from app.services.start_context import (
    NO_NAVIGATION_URL,
    derive_allowed_domains,
    goal_contains_url,
    resolve_start_context,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        # The resolver's only filter is "rows for this project", and the
        # fake session is constructed per-project, so filtering is a no-op
        # here. Kept as a method so the call chain matches the real one.
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    """Minimal stand-in for a SQLAlchemy Session.

    Dispatches db.get() by model class and returns pre-seeded rows for
    db.query(ProjectEnvironment).
    """

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
        assert model is ProjectEnvironment, f"unexpected db.query for {model}"
        return _FakeQuery(self._environments)


def _project(name="Vikaas", default_profile=None):
    return Project(
        id=uuid.uuid4(),
        name=name,
        # The project-wide login (migration 0042). Deliberately NOT on the
        # environment row any more — see test_login_resolves_even_when_no_
        # environment_matches for the failure that placement caused.
        default_credential_profile_id=(
            default_profile.id if default_profile is not None else None
        ),
    )


def _profile(*, kind=None, target_url=None, allowed_domains=None, creds="enc"):
    return AICredentialProfile(
        id=uuid.uuid4(),
        name="Env Profile",
        kind=kind,
        target_url=target_url,
        allowed_domains=allowed_domains,
        credentials_json=creds,
    )


def _env(project, environment="staging", base_url=None, default_profile=None):
    return ProjectEnvironment(
        id=uuid.uuid4(),
        project_id=project.id,
        environment=environment,
        base_url=base_url,
        default_credential_profile_id=(
            default_profile.id if default_profile is not None else None
        ),
    )


# ── The original bug ────────────────────────────────────────────────────────


def test_sow_skill_under_project_now_resolves_a_url():
    """The exact failing case: a skill with a project and an environment,
    no credential profile, and an instruction naming no URL."""
    proj = _project()
    db = _FakeDB(
        projects=[proj],
        environments=[_env(proj, "staging", "https://app.test/dashboard")],
    )

    ctx = resolve_start_context(
        db,
        project_id=proj.id,
        environment="staging",
        goal='Click the "Add Job" button within the Jobs module',
    )

    assert ctx.has_url
    assert ctx.environment_url == "https://app.test/dashboard"
    assert ctx.url_source == "project_environment"
    assert ctx.reason is None


def test_standard_profile_target_url_is_honoured():
    """A kind="standard" profile used to supply a password with nowhere to
    type it — target_url was read only inside the bypass branch."""
    proj = _project()
    prof = _profile(kind="standard", target_url="https://app.test/login")
    db = _FakeDB(projects=[proj], profiles=[prof])

    ctx = resolve_start_context(
        db, project_id=proj.id, credential_profile_id=prof.id, goal="click Add Job"
    )

    assert ctx.environment_url == "https://app.test/login"
    assert ctx.url_source == "credential_profile"
    assert ctx.profile is prof
    assert ctx.profile_source == "explicit"


def test_null_kind_profile_target_url_is_honoured():
    """kind=None means "standard" throughout the codebase — same fix."""
    proj = _project()
    prof = _profile(kind=None, target_url="https://app.test/home")
    db = _FakeDB(projects=[proj], profiles=[prof])

    ctx = resolve_start_context(db, project_id=proj.id, credential_profile_id=prof.id)

    assert ctx.environment_url == "https://app.test/home"


# ── Paths that already worked must not change ───────────────────────────────


def test_bypass_profile_still_wins_over_project_environment():
    proj = _project()
    prof = _profile(kind="bypass", target_url="https://bypass.test/dashboard")
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[_env(proj, "staging", "https://project.test/")],
    )

    ctx = resolve_start_context(
        db,
        project_id=proj.id,
        environment="staging",
        credential_profile_id=prof.id,
    )

    assert ctx.environment_url == "https://bypass.test/dashboard"
    assert ctx.url_source == "credential_profile"


def test_adhoc_target_url_wins_over_project_environment():
    proj = _project()
    db = _FakeDB(
        projects=[proj],
        environments=[_env(proj, "staging", "https://project.test/")],
    )

    ctx = resolve_start_context(
        db,
        project_id=proj.id,
        environment="staging",
        adhoc_target_url="https://adhoc.test/",
    )

    assert ctx.environment_url == "https://adhoc.test/"
    assert ctx.url_source == "adhoc"


def test_goal_with_embedded_url_is_not_reported_as_unrunnable():
    """Free-text "go to https://... and do X" goals navigate themselves and
    must stay exempt from the fail-fast gate."""
    db = _FakeDB()

    ctx = resolve_start_context(db, goal="Go to https://example.test/app and log in")

    assert not ctx.has_url
    assert ctx.reason is None  # None => the API gate lets it through


# ── Partial configuration ───────────────────────────────────────────────────


def test_profile_without_target_url_falls_back_to_project_base_url():
    """Credentials from the profile, address from the project."""
    proj = _project()
    prof = _profile(kind="standard", target_url=None)
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[_env(proj, "staging", "https://project.test/dash")],
    )

    ctx = resolve_start_context(
        db,
        project_id=proj.id,
        environment="staging",
        credential_profile_id=prof.id,
    )

    assert ctx.environment_url == "https://project.test/dash"
    assert ctx.url_source == "project_environment"
    assert ctx.profile is prof  # explicit profile kept


def test_project_default_credential_profile_is_adopted():
    prof = _profile(kind="standard", target_url=None, allowed_domains=["app.test"])
    proj = _project(default_profile=prof)
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[_env(proj, "staging", "https://app.test/x")],
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment="staging")

    assert ctx.profile is prof
    assert ctx.profile_source == "project_default"
    assert ctx.allowed_domains == ["app.test"]


# ── Login must not depend on the URL lookup ─────────────────────────────────
#
# These four pin the regression that made the first fix useless in
# practice: the login used to live on the ProjectEnvironment row, so it
# was only found if an environment label matched. A SOW-extracted skill
# has environment = NULL, so on any miss the login vanished, the run
# started unauthenticated, and the agent walked into the target app's
# real login form and hit its reCAPTCHA.


def test_login_resolves_even_when_no_environment_matches():
    """The exact regression. No environment row matches, but the login
    must still be attached."""
    prof = _profile(kind="bypass", target_url="https://app.test/jobs")
    proj = _project(default_profile=prof)
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[_env(proj, "production", "https://prod.test/")],
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment="staging")

    assert ctx.profile is prof
    assert ctx.profile_source == "project_default"
    # The bypass profile's own address is used, not the mismatched row's.
    assert ctx.environment_url == "https://app.test/jobs"


def test_login_resolves_with_no_environments_configured_at_all():
    prof = _profile(kind="bypass", target_url="https://app.test/jobs")
    proj = _project(default_profile=prof)
    db = _FakeDB(projects=[proj], profiles=[prof])

    ctx = resolve_start_context(db, project_id=proj.id, environment=None)

    assert ctx.profile is prof
    assert ctx.environment_url == "https://app.test/jobs"


def test_goal_with_embedded_url_still_gets_the_project_login():
    """A goal naming its own address waives the URL requirement — it must
    never suppress the login. This is what let a SOW skill whose text
    mentioned dev.interviewgod.ai run with no auth cookie at all."""
    prof = _profile(kind="bypass", target_url="https://app.test/jobs")
    proj = _project(default_profile=prof)
    db = _FakeDB(projects=[proj], profiles=[prof])

    ctx = resolve_start_context(
        db,
        project_id=proj.id,
        goal="Go to https://app.test/jobs and click the Add Job button",
    )

    assert ctx.profile is prof, "goal-embedded URL must not suppress the login"


def test_ambiguous_environment_still_yields_the_login():
    """Refusing to guess between dev and production loses the URL — it
    must not also lose the login."""
    prof = _profile(kind="standard", target_url=None, allowed_domains=["app.test"])
    proj = _project(default_profile=prof)
    db = _FakeDB(
        projects=[proj],
        profiles=[prof],
        environments=[
            _env(proj, "staging", "https://staging.test/"),
            _env(proj, "production", "https://prod.test/"),
        ],
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment=None, goal="click X")

    assert not ctx.has_url
    assert ctx.reason  # URL failure is still reported
    assert ctx.profile is prof  # but the login survived


def test_no_environment_label_uses_the_single_configured_environment():
    """SOW-extracted skills often carry no environment label at all."""
    proj = _project()
    db = _FakeDB(
        projects=[proj], environments=[_env(proj, "staging", "https://only.test/")]
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment=None)

    assert ctx.environment_url == "https://only.test/"


def test_no_environment_label_with_several_configured_refuses_to_guess():
    """Choosing between dev and production on the engineer's behalf is not
    a call this code gets to make silently."""
    proj = _project()
    db = _FakeDB(
        projects=[proj],
        environments=[
            _env(proj, "staging", "https://staging.test/"),
            _env(proj, "production", "https://prod.test/"),
        ],
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment=None, goal="click X")

    assert not ctx.has_url
    assert ctx.reason and "more than one" in ctx.reason


def test_unknown_environment_label_does_not_substitute_another():
    """A 'staging' skill must never silently run against production."""
    proj = _project()
    db = _FakeDB(
        projects=[proj], environments=[_env(proj, "production", "https://prod.test/")]
    )

    ctx = resolve_start_context(
        db, project_id=proj.id, environment="staging", goal="click X"
    )

    assert not ctx.has_url
    assert "prod.test" not in (ctx.environment_url or "")
    assert ctx.reason and "staging" in ctx.reason


def test_environment_label_match_is_case_insensitive():
    proj = _project()
    db = _FakeDB(
        projects=[proj], environments=[_env(proj, "Staging", "https://staging.test/")]
    )

    ctx = resolve_start_context(db, project_id=proj.id, environment="  staging ")

    assert ctx.environment_url == "https://staging.test/"


# ── Failure reporting ───────────────────────────────────────────────────────


def test_unconfigured_project_reports_the_project_by_name():
    proj = _project("Vikaas")
    db = _FakeDB(projects=[proj])

    ctx = resolve_start_context(db, project_id=proj.id, goal="click Add Job")

    assert not ctx.has_url
    assert ctx.environment_url == NO_NAVIGATION_URL
    assert ctx.reason and "Vikaas" in ctx.reason


def test_no_project_at_all_reports_a_usable_reason():
    db = _FakeDB()

    ctx = resolve_start_context(db, goal="click Add Job")

    assert not ctx.has_url
    assert ctx.reason and "not assigned to a project" in ctx.reason


def test_environment_row_without_base_url_is_not_treated_as_configured():
    proj = _project()
    db = _FakeDB(
        projects=[proj], environments=[_env(proj, "staging", base_url=None)]
    )

    ctx = resolve_start_context(
        db, project_id=proj.id, environment="staging", goal="click X"
    )

    assert not ctx.has_url
    assert ctx.reason


# ── Helpers ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("go to https://x.test and click", True),
        ("visit HTTP://X.TEST", True),
        ('Click the "Add Job" button in the Jobs module', False),
        ("", False),
        (None, False),
    ],
)
def test_goal_contains_url(goal, expected):
    assert goal_contains_url(goal) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://app.example.com/jobs", ["app.example.com"]),
        ("http://localhost:3000/x", ["localhost"]),
        (NO_NAVIGATION_URL, None),
        (None, None),
        ("", None),
    ],
)
def test_derive_allowed_domains(url, expected):
    assert derive_allowed_domains(url) == expected
