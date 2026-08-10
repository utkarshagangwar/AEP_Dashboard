"""Pydantic schemas for AI test run endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class FunctionalTestStep(BaseModel):
    """One atomic, orderable step authored in the Functional Test flow.
    Order is the position in AIRunCreate.steps, not a stored field."""
    text: str = Field(..., min_length=1, max_length=1000)


class FunctionalTestDataSet(BaseModel):
    """One named, parameterized data set for a Functional Test. Submitting
    N data sets creates N ai_test_runs rows (one per set) — see
    submit_run()'s data-driven expansion in app/api/v1/ai_runs.py."""
    name: str = Field(..., min_length=1, max_length=200)
    values: dict[str, str] = Field(default_factory=dict)

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
    # 20000 (up from 2000) -- large/multi-step Vibe Test goals (e.g. a long
    # numbered workflow spanning several pages) were hitting this ceiling
    # and getting rejected with a 422 before ever reaching the agent. The
    # backing column (AITestRun.goal) is already Text/unbounded in Postgres,
    # so raising this is just the request-schema side of the same "don't
    # artificially cap large runs" fix as ai_runner.py's step/duration caps.
    #
    # Optional as of the structured Functional Test flow: when
    # test_category="functional", goal is not authored directly — it's
    # compiled server-side from preconditions/steps/expected_results/
    # test_type/test_data (see submit_run() in app/api/v1/ai_runs.py) and
    # this field is ignored if sent. Every other caller (Android, Skill
    # Replay, Autonomous QA orchestrator) still sends goal directly and is
    # unaffected — see _validate_functional below for the exact gating.
    goal: Optional[str] = Field(default=None, max_length=20000)
    project_id: Optional[UUID] = None
    credential_profile_id: Optional[UUID] = None
    environment: Optional[str] = Field(None, max_length=200)
    # One-off "Website without/with login" path (non-"IG Automation"
    # environments) — mutually exclusive with credential_profile_id, never
    # persisted as a reusable profile.
    target_url: Optional[str] = None
    login_identifier: Optional[str] = Field(default=None, max_length=300)  # email/phone
    login_password: Optional[str] = Field(default=None, max_length=300)
    # "web" (default) | "android" — which Hands implementation executes this
    # run. See app.models.ai_runs.AITestRun.platform for why this is
    # orthogonal to everything above rather than folded into run_type.
    platform: str = Field(default="web", pattern="^(web|android)$")
    android_app_build_id: Optional[UUID] = None
    device_profile: Optional[str] = Field(default=None, max_length=100)

    # ── Structured Functional Test flow (New Vibe Test Phase 1) ────────────
    # Set test_category="functional" to author a test as preconditions +
    # ordered steps + expected results instead of a single free-text goal.
    # Leave unset (None) for every other existing caller — behavior for
    # those is completely unchanged.
    test_category: Optional[str] = Field(default=None, pattern="^(functional)$")
    preconditions: Optional[str] = Field(default=None, max_length=5000)
    steps: Optional[list[FunctionalTestStep]] = Field(default=None, max_length=100)
    expected_results: Optional[list[str]] = Field(default=None, max_length=50)
    test_data: Optional[list[FunctionalTestDataSet]] = Field(default=None, max_length=20)
    test_type: str = Field(default="happy", pattern="^(happy|negative|edge)$")
    # Free-text requirement/checkpoint reference — optional on both flows.
    linked_requirement: Optional[str] = Field(default=None, max_length=500)
    # "desktop" (default) | "tablet" | "mobile" — see
    # app.services.ai_runner.VIEWPORT_PRESETS. Functional Test only.
    viewport_preset: str = Field(default="desktop", pattern="^(desktop|tablet|mobile)$")

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

    @model_validator(mode="after")
    def _validate_functional(self):
        if self.test_category == "functional":
            if self.platform != "web":
                raise ValueError("Functional Test only supports platform='web' today")
            if not self.steps or len([s for s in self.steps if s.text.strip()]) == 0:
                raise ValueError("Functional Test requires at least one step")
            if not self.expected_results or len(
                [r for r in self.expected_results if r.strip()]
            ) == 0:
                raise ValueError("Functional Test requires at least one expected result")
        else:
            # Legacy/other callers (Android, Skill Replay, Autonomous QA
            # orchestrator): exact same requirement as before this feature.
            if not self.goal or len(self.goal.strip()) < 5:
                raise ValueError("goal must be at least 5 characters")
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
    # True once a full-session recording exists for this run (New Vibe
    # Test / Skill Replay only — see app/services/ai_run_capture.py). The
    # frontend builds its own proxied /video URL from this flag rather than
    # the backend dictating a frontend route shape, same as the stream URL.
    video_available: bool = False
    # Post-run DeepEval quality score (New Vibe Test / Skill Replay, web
    # platform only) -- see app/services/ai_eval.py. None for every other
    # run (Android, Autonomous QA, non-terminal status, pre-feature rows,
    # or scoring itself was unavailable).
    eval_score: Optional[float] = None
    eval_reason: Optional[str] = None
    eval_status: Optional[str] = None
    # Second, complementary judge pass (New Vibe Test Phase 4) — final-state
    # screenshot vs. this run's own expected_results. Functional Test only;
    # None for every other run or when scoring itself was unavailable. See
    # app/services/ai_eval.py's evaluate_expected_results.
    visual_eval_score: Optional[float] = None
    visual_eval_reason: Optional[str] = None
    visual_eval_status: Optional[str] = None
    # Structured Functional Test fields (New Vibe Test Phase 1) — all None
    # for a run created via any other flow (Android, Skill Replay,
    # Autonomous QA, or a pre-feature row).
    test_category: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[list[dict]] = None
    expected_results: Optional[list[str]] = None
    test_data: Optional[list[dict]] = None
    test_type: Optional[str] = None
    linked_requirement: Optional[str] = None
    viewport_preset: Optional[str] = None
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
    test_category: Optional[str] = None
    test_type: Optional[str] = None
    linked_requirement: Optional[str] = None
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
    # None = fully specified by its source and runnable as written.
    # "needs_review" / "needs_design_flow" mean the source document named
    # this requirement without specifying it well enough to execute; the
    # skill exists so the gap is visible and assignable, not so it can be
    # run as-is. review_reason says what specifically is missing.
    review_status: Optional[str] = None
    review_reason: Optional[str] = None
    # ── TDD classification (app.services.tdd_extraction) ──
    # All Optional: skills created before the v2 extractor carry None, which
    # the UI renders as "unclassified" rather than guessing a value.
    #
    # test_type is the one a reviewer cannot do without: a "negative" skill
    # PASSES when the system refuses the action, so a red result on a
    # negative skill means the product ACCEPTED something it should have
    # rejected — the opposite reading from a red positive skill.
    test_type: Optional[str] = None          # positive | negative | edge
    category: Optional[str] = None           # tdd_extraction.CATEGORIES code
    # stated = the source document specifies this expectation.
    # derived = inferred from standard QA practice; a failure here may be a
    # spec gap rather than a product defect.
    grounding: Optional[str] = None
    # Shared by every variant of one behaviour — lets the Skills tab group
    # positive/negative/edge for the same feature together.
    behaviour_key: Optional[str] = None
    priority: Optional[str] = None           # smoke | sanity | regression
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


# ── Coverage report (New Vibe Test Phase 6, A.4/D.15) ───────────────────────
#
# "Requirement -> linked test(s) -> latest status -> (functional) GEval
# score -> last-run date" per the checklist. Neither AITestRun nor VisualRun
# has a "test case" identity distinct from an individual run row -- grouping
# key is computed at query time, not stored (see get_coverage() in
# app/api/v1/ai_runs.py): a functional test group is
# (linked_requirement, sha256-normalized-goal) — the exact same goal_hash
# app.services.skill_store.compute_goal_hash already uses for Skill
# auto-save identity, so re-submitting the identical structured Functional
# Test fields always lands in the same group. A UI test group is
# (linked_requirement, target_url, artifact_id) — same reference design +
# same page compared again is "the same test"; a different reference or
# page is a different one even under the same requirement tag. Per explicit
# product decision (2026-07-28) over the simpler "group by requirement
# alone" option, which would have blended different tests' pass/fail
# history into one misleading rate whenever they happened to share a
# requirement tag.


class CoverageTestEntry(BaseModel):
    kind: str  # "functional" | "ui"
    label: str  # goal (truncated) for functional, target_url for ui
    test_type: Optional[str] = None  # functional only: happy | negative | edge
    latest_run_id: UUID
    latest_status: str
    last_run_at: datetime
    # Functional only — None for a ui entry or if scoring was unavailable.
    latest_eval_score: Optional[float] = None
    # UI only — None for a functional entry.
    latest_pixel_mismatch_pct: Optional[int] = None
    total_runs: int
    pass_count: int
    fail_count: int
    # Functional only (VisualRun has no needs_review-equivalent status) —
    # always 0 for a ui entry.
    needs_review_count: int = 0
    # pass_count / (pass_count + fail_count), excluding needs_review/
    # inconclusive/cancelled/pending/running from the denominator entirely
    # — an undecided or never-finished run shouldn't silently count against
    # (or for) a test's reliability. None if there's no terminal run yet.
    pass_rate: Optional[float] = None
    # New Vibe Test Phase 7 (F.26) — replaces CoverageTab's old crude
    # "pass_count > 0 and fail_count > 0" Flaky badge, which flagged a test
    # that failed once three months ago and has passed cleanly ever since
    # exactly the same as one that alternates every run. Computed over the
    # SAME decided-only (passed/failed) run sequence as pass_rate, in
    # chronological order: the fraction of consecutive decided-run pairs
    # whose status differs. A test that's always passed or always failed is
    # 0.0 (consistent, not flaky, regardless of how many runs). A test that
    # alternates every single run approaches 1.0. None if there are fewer
    # than 2 decided runs — not enough history to call it either way.
    flakiness_rate: Optional[float] = None


class CoverageRequirementGroup(BaseModel):
    linked_requirement: str
    functional_tests: list[CoverageTestEntry] = []
    ui_tests: list[CoverageTestEntry] = []


class CoverageResponse(BaseModel):
    requirements: list[CoverageRequirementGroup] = []
    # Runs that exist but have no linked_requirement set — surfaced as a
    # single count (not enumerated) so the report can say "N test(s) have no
    # linked requirement" instead of silently omitting them with no trace.
    unlinked_functional_count: int = 0
    unlinked_ui_count: int = 0


class SpecGapEntry(BaseModel):
    """One derived expectation that the product has never once satisfied.

    A `derived` skill (app.services.tdd_extraction) asserts something the
    source document never actually stated — it was inferred from standard QA
    practice, because documents rarely enumerate their own failure modes.
    When such a test fails there are two possible explanations and they need
    opposite responses:

      * the product is wrong  -> raise a defect
      * the INFERENCE is wrong, because the product deliberately behaves
        differently and the document simply never said so -> fix the document

    Nothing in the pipeline could tell those apart, so in practice every
    failing derived test was triaged as the first. This entry surfaces the
    ones where the second is likely.
    """

    skill_id: UUID
    name: str
    test_type: Optional[str] = None       # negative | edge
    category: Optional[str] = None
    behaviour_key: Optional[str] = None
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    source_type: Optional[str] = None     # sow | video
    source_artifact_id: Optional[UUID] = None
    # Decided runs only (passed/failed). needs_review, inconclusive,
    # cancelled, pending and running are excluded from BOTH counts — an
    # undecided run is not evidence either way.
    decided_runs: int = 0
    fail_count: int = 0
    last_run_at: Optional[str] = None
    # The agent's own words on the most recent failure. Usually the fastest
    # way for a human to tell "the product refused differently than we
    # guessed" from "the product genuinely accepted something it shouldn't".
    last_failure_summary: Optional[str] = None


class SpecGapResponse(BaseModel):
    entries: list[SpecGapEntry] = []
    # Denominators, so the headline number can be read honestly. A report of
    # "7 spec gaps" means something different against 12 derived skills than
    # against 400.
    total_derived_skills: int = 0
    # Derived skills with at least min_runs decided runs — the only ones that
    # could have qualified. The rest have simply not been run enough yet.
    evaluated_skills: int = 0
    min_runs: int = 2
