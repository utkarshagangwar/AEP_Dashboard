"""Pydantic schemas for project endpoints."""
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    """Request body for creating a project."""

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    environments: Optional[list[str]] = None


class ProjectUpdate(BaseModel):
    """Request body for updating a project (PATCH semantics)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    environments: Optional[list[str]] = None


class ProjectResponse(BaseModel):
    """Response schema for a single project."""

    id: UUID
    name: str
    description: Optional[str] = None
    is_active: bool
    environments: Optional[list[str]] = None
    suite_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Envelope for the project list endpoint, matching the {data, total,
    page, limit} shape used by every other list endpoint in the API."""

    data: list[ProjectResponse]
    total: int
    page: int
    limit: int


class ProjectDetailResponse(ProjectResponse):
    """Project detail response including nested test suites."""

    suites: list["SuiteSummary"] = []


class SuiteSummary(BaseModel):
    """Minimal suite info nested in project detail."""

    id: UUID
    name: str
    suite_type: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Project environments (migration 0041) ───────────────────────────────────
#
# `Project.environments` is an ARRAY of bare labels ("dev", "staging",
# "production") with nothing behind them. These schemas back the table that
# gives a label a real address, so a run scoped to a project — notably a
# prompt skill extracted from a SOW, which is saved under a project but
# never given a credential profile at parse time — can resolve somewhere to
# actually navigate to instead of opening a blank tab.


class ProjectTestSetup(BaseModel):
    """The whole "Test setup" popup in one object.

    One request instead of the previous two-step (pick environment, then
    save a row): the user makes one decision — which login — and the
    start URL follows from it. Optional fields left as None are cleared,
    which is what "None (no login)" and an emptied URL field must mean.
    """

    # The project-wide default login. Lives on the project, not on an
    # environment row — see Project.default_credential_profile_id.
    default_credential_profile_id: Optional[UUID] = None
    # Only meaningful when there is no login, or when the user explicitly
    # overrides the login's own target_url under "Advanced".
    environment: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_base_url(v)


class ProjectTestSetupResponse(BaseModel):
    """What the Test setup popup renders from.

    `start_url` is the resolved answer the backend will actually use, so
    the popup can show the user exactly where their tests will start
    rather than making them infer it from a login profile plus a base
    URL. `start_url_source` says which of those it came from.
    """

    project_id: UUID
    default_credential_profile_id: Optional[UUID] = None
    default_credential_profile_name: Optional[str] = None
    default_credential_profile_kind: Optional[str] = None
    #: Where runs will actually start, or None if nothing resolves yet.
    start_url: Optional[str] = None
    #: "credential_profile" | "project_environment" | "none"
    start_url_source: str = "none"
    #: True when the project is runnable as configured.
    is_ready: bool = False
    #: Plain-language explanation when is_ready is False.
    reason: Optional[str] = None
    environments: list["ProjectEnvironmentResponse"] = []


def _normalize_base_url(v: Optional[str]) -> Optional[str]:
    """Shared base_url validation.

    Rejects a bare host: Playwright's page.goto() cannot navigate to
    "app.example.com", and letting one through reproduces the exact
    blank-page failure this feature exists to eliminate, one layer later
    and harder to diagnose.
    """
    if v is None:
        return None
    v = v.strip()
    if not v:
        # An emptied field means "clear this", a legitimate operation.
        # Normalize to NULL rather than storing "" which reads as set.
        return None
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            "base_url must be an absolute http(s) URL, e.g. "
            "https://app.example.com/dashboard"
        )
    return v


class ProjectEnvironmentUpsert(BaseModel):
    """Set (or clear) a project environment's address and default login.

    Upsert semantics keyed on (project, environment): PUTting the same
    label twice updates in place rather than accumulating duplicates.
    """

    environment: str = Field(..., min_length=1, max_length=200)
    # Validated as an absolute http(s) URL rather than accepted as free
    # text: a bare host like "app.example.com" is not something Playwright's
    # page.goto() can navigate to, and letting one through would reproduce
    # the exact blank-page failure this feature exists to eliminate — just
    # one layer later and harder to diagnose.
    base_url: Optional[str] = Field(default=None, max_length=2000)
    default_credential_profile_id: Optional[UUID] = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_base_url(v)


class ProjectEnvironmentResponse(BaseModel):
    id: UUID
    project_id: UUID
    environment: str
    base_url: Optional[str] = None
    default_credential_profile_id: Optional[UUID] = None
    # Denormalized for display, so the settings UI does not need a second
    # round-trip to name the profile. Same convention as
    # AITestRun.credential_profile_name.
    default_credential_profile_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
