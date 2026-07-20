"""Pydantic schemas for AI test run endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# Fields required in `credentials` when kind="bypass" — see
# app/workers/tasks/ai_execution.py::_resolve_bypass_profile for how they're
# used (an admin API-key login call, not a typed-in-form login). The X-API-Key
# header alone is sufficient — the endpoint grants access directly from the
# key, no email/otp identity needed (confirmed against the actual endpoint
# behavior; do not reintroduce an email/otp requirement here).
BYPASS_REQUIRED_CREDENTIAL_FIELDS = {"api_base_url", "api_key", "cookie_domain"}


class CredentialProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    project_id: Optional[UUID] = None
    # None/"standard" = plain username+password (today's only kind). "bypass"
    # = inject an auth cookie via an admin API-key login call instead of
    # typing into a login form.
    kind: Optional[str] = Field(default=None, max_length=20)
    # Only meaningful for kind="bypass" — where to navigate after the auth
    # cookie is injected.
    target_url: Optional[str] = None
    allowed_domains: Optional[list[str]] = None
    # standard: {username, password}-shaped (any keys — passed straight to
    # browser-use's sensitive_data). bypass: {api_base_url, bypass_endpoint,
    # api_key, cookie_name, cookie_domain}.
    credentials: Optional[dict] = None

    @model_validator(mode="after")
    def _validate_bypass(self):
        if self.kind == "bypass":
            if not self.target_url:
                raise ValueError("target_url is required for a bypass credential profile")
            if not self.allowed_domains:
                raise ValueError("allowed_domains is required for a bypass credential profile")
            missing = BYPASS_REQUIRED_CREDENTIAL_FIELDS - (self.credentials or {}).keys()
            if missing:
                raise ValueError(
                    f"credentials missing required bypass field(s): {', '.join(sorted(missing))}"
                )
        return self


class CredentialProfileResponse(BaseModel):
    id: UUID
    name: str
    project_id: Optional[UUID] = None
    kind: Optional[str] = None
    target_url: Optional[str] = None
    allowed_domains: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIRunCreate(BaseModel):
    goal: str = Field(..., min_length=5, max_length=2000)
    project_id: Optional[UUID] = None
    credential_profile_id: Optional[UUID] = None
    environment: Optional[str] = Field(None, max_length=200)
    # One-off "Website without/with login" path (Quick mode, non-"IG
    # Automation" environments) — mutually exclusive with
    # credential_profile_id, never persisted as a reusable profile.
    target_url: Optional[str] = None
    login_identifier: Optional[str] = Field(default=None, max_length=300)  # email/phone
    login_password: Optional[str] = Field(default=None, max_length=300)
    # "web" (default) | "android" — which Hands implementation executes this
    # run. See app.models.ai_runs.AITestRun.platform for why this is
    # orthogonal to everything above rather than folded into run_type.
    platform: str = Field(default="web", pattern="^(web|android)$")
    android_app_build_id: Optional[UUID] = None
    device_profile: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_adhoc(self):
        if self.credential_profile_id and (
            self.target_url or self.login_identifier or self.login_password
        ):
            raise ValueError(
                "Provide either credential_profile_id or ad-hoc target_url/login fields, not both"
            )
        if bool(self.login_identifier) != bool(self.login_password):
            raise ValueError("login_identifier and login_password must be provided together")
        return self

    @model_validator(mode="after")
    def _validate_platform(self):
        if self.platform == "android":
            if not self.android_app_build_id:
                raise ValueError(
                    "android_app_build_id is required when platform='android'"
                )
            if self.target_url or self.login_identifier or self.login_password:
                raise ValueError(
                    "target_url/login_identifier/login_password are web-only, "
                    "not valid with platform='android'"
                )
        elif self.android_app_build_id or self.device_profile:
            raise ValueError(
                "android_app_build_id/device_profile are only valid when platform='android'"
            )
        return self


class AIRunEventResponse(BaseModel):
    id: UUID
    run_id: UUID
    sequence: int
    status: str
    description: str
    step_type: str
    elapsed_ms: Optional[int] = None
    screenshot_url: Optional[str] = None
    highlighted_element: Optional[dict] = None
    is_failing_step: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class OrchestratorDecisionResponse(BaseModel):
    step: str
    invoked: bool
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    is_deterministic: bool = True
    rationale: str
    sequence: int = 0

    model_config = {"from_attributes": True}


class VisualFindingResponse(BaseModel):
    engine: str
    severity: str
    element: Optional[str] = None
    issue: str
    expected: Optional[str] = None
    actual: Optional[str] = None

    model_config = {"from_attributes": True}


class AIRunResponse(BaseModel):
    id: UUID
    goal: str
    environment: Optional[str] = None
    project_id: Optional[UUID] = None
    credential_profile_id: Optional[UUID] = None
    credential_profile_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    step_count: int = 0
    summary: Optional[str] = None
    raw_summary: Optional[str] = None
    run_type: str = "ai"
    skill_id: Optional[UUID] = None
    failing_step_index: Optional[int] = None
    failing_step_description: Optional[str] = None
    failing_step_screenshot_url: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    events: list[AIRunEventResponse] = []
    # "web" (default) | "android".
    platform: str = "web"
    android_app_build_id: Optional[UUID] = None
    android_app_build_name: Optional[str] = None
    device_profile: Optional[str] = None
    # Android-only: {farm_vendor, farm_session_id, dashboard_url, video_url}.
    platform_metadata: Optional[dict] = None
    # Autonomous QA (orchestrator) runs only — empty/None for plain "ai" runs.
    error_message: Optional[str] = None
    ai_test_run_id: Optional[UUID] = None
    visual_run_id: Optional[UUID] = None
    self_execute_answer: Optional[str] = None
    pixel_mismatch_pct: Optional[int] = None
    decisions: list[OrchestratorDecisionResponse] = []
    findings: list[VisualFindingResponse] = []

    model_config = {"from_attributes": True}


class AIRunListItem(BaseModel):
    id: UUID
    goal: str
    environment: Optional[str] = None
    credential_profile_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    step_count: int = 0
    run_type: str = "ai"
    platform: str = "web"
    created_at: datetime

    model_config = {"from_attributes": True}


class AndroidAppBuildResponse(BaseModel):
    id: UUID
    name: str
    project_id: Optional[UUID] = None
    apk_filename: str
    file_size: Optional[int] = None
    farm_vendor: str = "browserstack"
    package_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AIRunListResponse(BaseModel):
    data: list[AIRunListItem]
    total: int
    page: int
    limit: int


class AISkillResponse(BaseModel):
    id: UUID
    name: str
    goal: str
    source_run_id: Optional[UUID] = None
    # "sow" | "video" | None (None = auto-saved from a passed goal-based run)
    source_type: Optional[str] = None
    source_artifact_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    # Denormalized for display — resolved by the endpoint, not stored.
    project_name: Optional[str] = None
    environment: Optional[str] = None
    credential_profile_id: Optional[UUID] = None
    # False until this skill has actually been run once and passed — a
    # prompt-only skill has no recorded actions to replay deterministically.
    has_recording: bool = False
    # True once a human has edited name/goal/project by hand — protects the
    # edit from being overwritten the next time its source SOW/video part
    # is re-analyzed.
    manually_edited: bool = False
    step_count: int = 0
    times_replayed: int = 0
    last_replay_status: Optional[str] = None
    last_replayed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AISkillListResponse(BaseModel):
    data: list[AISkillResponse]
    total: int
    page: int
    limit: int


class AISkillUpdate(BaseModel):
    """Partial update for manual view/edit from the Skills tab. Only fields
    present in the request body are applied (exclude_unset) — project_id may
    be explicitly set to null to unassign a skill from any project."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    goal: Optional[str] = Field(default=None, min_length=1)
    project_id: Optional[UUID] = None


class SkillReplayRequest(BaseModel):
    credential_profile_id: Optional[UUID] = None
    allow_ai_fallback: bool = False


class BulkSkillIds(BaseModel):
    """Shared body shape for bulk skill operations — a plain list of target
    IDs. Capped at 200 to keep a single request/transaction bounded; the
    Skills tab only ever selects from one page (max 100) at a time."""
    skill_ids: list[UUID] = Field(min_length=1, max_length=200)


class BulkAssignProjectRequest(BulkSkillIds):
    # None unassigns the selected skills from any project.
    project_id: Optional[UUID] = None
