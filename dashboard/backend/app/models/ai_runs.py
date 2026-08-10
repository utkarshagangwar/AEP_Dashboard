"""ORM models for AI test run tables."""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from app.core.database import Base


class AIRunStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"
    inconclusive = "inconclusive"
    cancelled = "cancelled"
    # New Vibe Test Phase 4 (2026-07-28): the agent self-reported "passed",
    # but the independent GEval quality score (app/services/ai_eval.py) came
    # back below threshold -- neither "passed" (the self-report is disputed)
    # nor "failed" (the agent did complete something; a human should decide,
    # not the gate) is right, so this is its own terminal state. Only ever
    # assigned by _persist_result's gating in
    # app/workers/tasks/ai_execution.py; the engine itself never returns
    # this status string directly. See AITestRun.eval_score/eval_reason for
    # why.
    needs_review = "needs_review"


class AIEventStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"


class AIStepType(str, PyEnum):
    deterministic = "deterministic"
    ai_scoped = "ai_scoped"


class AICredentialProfile(Base):
    __tablename__ = "ai_credential_profiles"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = mapped_column(String(200), nullable=False)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    allowed_domains = mapped_column(JSONB, nullable=True)
    credentials_json = mapped_column(Text, nullable=True)
    # null/"standard" = plain username+password via sensitive_data (today's
    # only kind). "bypass" = inject an auth cookie obtained via an admin
    # API-key login call instead of typing into a login form — routes around
    # CAPTCHA-gated forms the AI agent cannot and should not try to solve.
    # For "bypass", credentials_json holds {api_base_url, bypass_endpoint,
    # api_key, cookie_name, cookie_domain} instead of {username, password}.
    # The API key alone grants access — no separate user identity needed.
    kind = mapped_column(String(20), nullable=True)
    # Only meaningful for kind="bypass" — where to navigate after the auth
    # cookie is injected. Should be the actual logged-in destination (e.g.
    # .../dashboard), not the public marketing homepage — the homepage
    # typically renders the same nav regardless of auth state.
    target_url = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Values for AISkill.review_status (migration 0040). Plain constants rather
# than a DB Enum, matching the SOW_SOURCE_STAGE_* convention in
# app/models/sow.py: this vocabulary is expected to grow, and Postgres enum
# values cannot be removed once added (see migration 0036's downgrade note).
# An unrecognised value degrades to "flagged" in the UI, never breaks it.
SKILL_REVIEW_READY = None            # fully specified — runnable as written
SKILL_REVIEW_NEEDS_REVIEW = "needs_review"
# The document implies a user flow it never actually describes (names a
# screen but no route to it, an outcome but no trigger). Distinct from
# needs_review because the fix is a design decision, not a clarification.
SKILL_REVIEW_NEEDS_DESIGN_FLOW = "needs_design_flow"

SKILL_REVIEW_STATUSES = (SKILL_REVIEW_NEEDS_REVIEW, SKILL_REVIEW_NEEDS_DESIGN_FLOW)


class AISkill(Base):
    """A reusable skill in the Vibe Testing "Skills" tab — either a recorded
    action replay or a prompt-only instruction, distinguished by whether
    history_json is set.

    Recorded skills: history_json stores the browser-use AgentHistoryList
    (screenshots stripped) captured from a passed goal-based AI test run, so
    the run can be replayed via Agent.rerun_history() without any LLM
    planning calls.

    Prompt skills: history_json is None. Saved directly from SOW/video
    checkpoint parsing (see app.services.skill_store) — a detailed,
    step-by-step instruction an AI agent can execute, with no live browser
    run required to produce it. Running one (from the Skills tab) is a
    normal AI-planned run; if it passes, the goal-based auto-save path
    upgrades this same row (matched by goal_hash) with a real recording.

    A skill can also be viewed/edited by hand from the Skills tab (name,
    goal text, project). Editing sets manually_edited=True and, if the goal
    text changed on a recorded skill, clears history_json/step_count — the
    old recording no longer matches the edited instructions, so the next
    run re-plans with AI and records fresh actions instead of silently
    replaying stale ones.
    """

    __tablename__ = "ai_skills"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = mapped_column(String(300), nullable=False)
    goal = mapped_column(Text, nullable=False)
    goal_hash = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Set only for a prompt skill extracted from a SOW/video checkpoint.
    # SET NULL (not CASCADE) on document delete — a skill may have already
    # been run and upgraded to a recorded one, decoupled from its source doc.
    source_type = mapped_column(String(20), nullable=True)  # "sow" | "video" | null
    source_artifact_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable identity for prompt-skill upserts (artifact_id + normalized
    # checkpoint title) — re-analyzing a part updates this row in place
    # instead of duplicating it. Null for goal-based recorded skills, which
    # upsert by goal_hash instead.
    source_key = mapped_column(String(300), nullable=True, unique=True, index=True)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # ── TDD classification (app.services.tdd_extraction, migration 0043) ──
    # All nullable with no backfill. Skills created before the v2 extractor
    # carry NULLs, which every reader treats as "positive / stated /
    # regression, category unknown" — the conservative reading of a legacy
    # happy-path skill, which is exactly what they are.
    #
    # test_type: "positive" | "negative" | "edge". The most operationally
    #   important of these: a negative skill PASSES when the system refuses
    #   the action, so a reviewer reading a "failed" result needs this to
    #   interpret it correctly.
    test_type = mapped_column(String(20), nullable=True, index=True)
    # category: a code from tdd_extraction.CATEGORIES — the behaviour class
    #   that determined which variants were required (e.g. "authorization",
    #   "ai_untrusted_input"). Drives coverage reporting per risk area.
    category = mapped_column(String(50), nullable=True, index=True)
    # grounding: "stated" (the source document specifies this expectation) or
    #   "derived" (inferred from standard QA practice because the document is
    #   silent). A failing "derived" test may be a spec gap rather than a
    #   product defect, and triage needs to be able to tell.
    grounding = mapped_column(String(20), nullable=True)
    # behaviour_key: stable slug shared by every variant of one behaviour, so
    #   the Skills tab can group "Create Job" positive/negative/edge together
    #   and coverage can be reported per behaviour rather than per row.
    behaviour_key = mapped_column(String(120), nullable=True, index=True)
    # priority: "smoke" | "sanity" | "regression" — the execution tier, which
    #   maps onto Robot Framework [Tags] for suite selection.
    priority = mapped_column(String(20), nullable=True)

    environment = mapped_column(String(200), nullable=True)
    credential_profile_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    history_json = mapped_column(Text, nullable=True)
    step_count = mapped_column(Integer, default=0)
    times_replayed = mapped_column(Integer, default=0, nullable=False)
    last_replay_status = mapped_column(String(20), nullable=True)
    last_replayed_at = mapped_column(DateTime, nullable=True)
    # True once a human has edited name/goal/project via the Skills tab.
    # Protects that edit from being silently clobbered the next time the
    # source SOW/video part is re-analyzed (see skill_store.upsert_prompt_skill).
    manually_edited = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Whether this skill is actually runnable as written, or needs a human to
    # fill in something the source document never specified (migration 0040).
    # One of the SKILL_REVIEW_* constants below; NULL/"ready" means runnable.
    #
    # This exists because the alternative was worse: a requirement the source
    # described but did not specify well enough to write steps for used to be
    # dropped silently, so it produced no skill and no trace. Nothing is
    # marked ready by default — an under-specified requirement is captured
    # and flagged, never quietly omitted and never quietly passed off as
    # complete.
    review_status = mapped_column(String(30), nullable=True, index=True)
    # What specifically is missing, in the model's own words, e.g. "the
    # document names the Export button but never states what it produces".
    review_reason = mapped_column(Text, nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def has_recording(self) -> bool:
        return self.history_json is not None


class AndroidAppBuild(Base):
    """An uploaded Android debug APK, pushed to a cloud device farm
    (BrowserStack App Automate today — see app.services.device_farm) and
    referenced by farm_app_id (e.g. "bs://<hash>") for Android Vibe Testing
    runs. Reusable across runs, like a credential profile — not a
    QA-cycle-scoped artifact — hence project_id is ondelete=SET NULL rather
    than CASCADE.

    The original APK bytes are kept on the shared visual_qa_data-style
    volume (storage_path) even after upload, because BrowserStack expires an
    uploaded app after ~30 days of inactivity — keeping the file lets a
    stale farm_app_id be refreshed by re-upload instead of asking the QA
    engineer to re-locate the APK.
    """

    __tablename__ = "android_app_builds"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = mapped_column(String(300), nullable=False)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    apk_filename = mapped_column(String(500), nullable=False)
    sha256 = mapped_column(String(64), nullable=False, index=True)
    storage_path = mapped_column(Text, nullable=True)
    file_size = mapped_column(Integer, nullable=True)
    # Only "browserstack" is implemented today — kept as a column (rather
    # than assumed) so a second vendor is an additive change, not a migration.
    farm_vendor = mapped_column(String(20), nullable=False, server_default="browserstack")
    farm_app_id = mapped_column(Text, nullable=False)  # e.g. "bs://<hash>"
    # Informational only — captured live from driver.current_package on the
    # build's first run rather than requiring APK parsing at upload time.
    package_name = mapped_column(String(300), nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AITestRun(Base):
    __tablename__ = "ai_test_runs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goal = mapped_column(Text, nullable=False)
    environment = mapped_column(String(200), nullable=True)
    project_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_profile_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    credential_profile_name = mapped_column(String(200), nullable=True)
    # One-off "Website without/with login" path — used only when no saved
    # credential_profile_id is set. Never persisted as a reusable profile.
    adhoc_target_url = mapped_column(Text, nullable=True)
    # Fernet-encrypted {"username": ..., "password": ...}, same
    # credential_service helpers as AICredentialProfile.credentials_json —
    # never store the ad-hoc password in plaintext, even though it's one-off.
    adhoc_credentials_json = mapped_column(Text, nullable=True)
    # "web" (default) | "android" — which Hands implementation executes this
    # run (app.services.ai_runner vs app.services.android_runner).
    # Deliberately orthogonal to run_type below (execution-origin: "ai" vs
    # "skill_replay") — same separation the frontend already keeps between
    # testType (web/android) and testMode (quick/visual/sow/video). Plain
    # string discriminator, not a native enum, matching AISkill.source_type's
    # existing convention for a small, still-growing set of values.
    platform = mapped_column(String(20), nullable=False, server_default="web")
    android_app_build_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("android_app_builds.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Denormalized display fallback, same pattern as credential_profile_name.
    android_app_build_name = mapped_column(String(300), nullable=True)
    # Key into app.services.device_farm.DEVICE_PROFILES — not a live farm
    # catalog fetch for MVP.
    device_profile = mapped_column(String(100), nullable=True)
    # Android-only structured metadata: {farm_vendor, farm_session_id,
    # dashboard_url, video_url}. Always null for web runs. Structured data an
    # API/UI needs to read directly — same shape decision already made for
    # AIRunEvent.highlighted_element, rather than scraping it out of prose.
    platform_metadata = mapped_column(JSONB, nullable=True)
    status = mapped_column(
        Enum(AIRunStatus, name="ai_run_status_enum"),
        nullable=False,
        default=AIRunStatus.pending,
    )
    started_at = mapped_column(DateTime, nullable=True)
    completed_at = mapped_column(DateTime, nullable=True)
    duration_ms = mapped_column(Integer, nullable=True)
    step_count = mapped_column(Integer, default=0)
    summary = mapped_column(Text, nullable=True)
    raw_summary = mapped_column(Text, nullable=True)
    run_type = mapped_column(String(20), nullable=False, server_default="ai")
    skill_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_skills.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    failing_step_index = mapped_column(Integer, nullable=True)
    failing_step_description = mapped_column(Text, nullable=True)
    failing_step_screenshot_url = mapped_column(Text, nullable=True)
    # Server-side path to the full-session recording on the visual_qa_data
    # volume (see app/services/ai_run_capture.py). Null = no video — either
    # a legacy run, capture failed, or this run never opted in (Autonomous
    # QA's Hands sub-step and Android runs never set this).
    video_path = mapped_column(Text, nullable=True)
    # Post-run DeepEval (GEval) quality score -- see app/services/ai_eval.py.
    # Web-platform "ai"/"skill_replay" runs only, and only once the run
    # reaches a terminal passed/failed status (see _persist_result's gating
    # in app/workers/tasks/ai_execution.py); always null for Android runs,
    # Autonomous QA (a separate table/pipeline), and pre-feature rows.
    eval_score = mapped_column(Float, nullable=True)
    eval_reason = mapped_column(Text, nullable=True)
    # "completed" | "unavailable" | null (never attempted). Plain string,
    # not a native enum -- same convention as AISkill.source_type for a
    # small, still-growing set of values.
    eval_status = mapped_column(String(20), nullable=True)
    eval_metric = mapped_column(String(50), nullable=True)
    # Second, complementary judge pass (New Vibe Test Phase 4) --
    # app.services.ai_eval.evaluate_expected_results() -- a final-state
    # screenshot compared against this row's own `expected_results`
    # (Functional Test only), independent of the action-trace eval_score
    # above. Same "completed" | "unavailable" | null (never attempted)
    # convention as eval_status; always null for a non-Functional-Test run
    # (no expected_results to check against) or when no final screenshot
    # was available.
    visual_eval_score = mapped_column(Float, nullable=True)
    visual_eval_reason = mapped_column(Text, nullable=True)
    visual_eval_status = mapped_column(String(20), nullable=True)
    visual_eval_metric = mapped_column(String(50), nullable=True)
    # ── Structured Functional Test fields (New Vibe Test Phase 1) ──────────
    # 'functional' for a run created via the new structured Functional Test
    # flow; null for every pre-existing row and for rows still created via a
    # plain goal (Android, Skill Replay, Autonomous QA orchestrator) — those
    # paths are unmodified. `goal` above is still always populated: for a
    # functional-test row it holds the goal text compiled server-side from
    # the fields below, so every existing consumer of `goal` (ai_runner.py,
    # ai_eval.py, Skill auto-save, the Results tab) needs no changes.
    test_category = mapped_column(String(20), nullable=True)
    preconditions = mapped_column(Text, nullable=True)
    steps = mapped_column(JSONB, nullable=True)  # ordered list of atomic step strings
    expected_results = mapped_column(JSONB, nullable=True)  # list of strings
    # List of {name, values: {...}} named data sets. One AITestRun row is
    # created per data set at submit time (data-driven execution) — this
    # column records which full set of data sets the parent submission
    # offered, not just the single set this particular row used.
    test_data = mapped_column(JSONB, nullable=True)
    test_type = mapped_column(String(20), nullable=True)  # happy | negative | edge
    # Free-text requirement/checkpoint reference. Same column name/shape as
    # VisualRun.linked_requirement so UI and functional tests can eventually
    # be reported against one coverage view (Phase 6).
    linked_requirement = mapped_column(String(500), nullable=True)
    # "desktop" (default/null) | "tablet" | "mobile" — see
    # app.services.ai_runner.VIEWPORT_PRESETS. Functional Test only (Phase
    # 2); null for every other run keeps today's fixed desktop viewport.
    viewport_preset = mapped_column(String(20), nullable=True)
    created_by = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AIRunEvent(Base):
    __tablename__ = "ai_run_events"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_test_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = mapped_column(Integer, nullable=False)
    status = mapped_column(
        Enum(AIEventStatus, name="ai_event_status_enum"),
        nullable=False,
        default=AIEventStatus.pending,
    )
    description = mapped_column(Text, nullable=False)
    step_type = mapped_column(
        Enum(AIStepType, name="ai_step_type_enum"),
        nullable=False,
        default=AIStepType.deterministic,
    )
    elapsed_ms = mapped_column(Integer, nullable=True)
    screenshot_url = mapped_column(Text, nullable=True)
    highlighted_element = mapped_column(JSONB, nullable=True)
    is_failing_step = mapped_column(Boolean, default=False)
    created_at = mapped_column(DateTime, server_default=func.now(), nullable=False)
