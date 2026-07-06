"""The Orchestrator ("The Brain") — coordinates Hands, Judge, and
self-execution per task, modeled on Sakana AI's Fugu: decide first, using
deterministic rules for hard constraints and one lightweight LLM judgment
call only for genuine ambiguity, then either solve the task directly or
delegate to exactly the sub-agent(s) it actually needs — never both by
default, never neither by accident.

Reuses (does not replace) the existing services:
  - app.services.ai_runner ("the Hands") — resolve_with_ai()/run_ai_test_sync(),
    steered via its llm_override parameter.
  - app.services.visual_judge ("the Judge") — judge(), steered via its
    model_override parameter (which itself may self-execute by skipping the
    vision pass when pixel-diff is already conclusive — see visual_judge.py).
  - app.services.llm_router — complete(), used both for the coordinator's own
    classification call and for the "self_execute" path (pure text tasks
    that need neither sub-agent), steered via model_override.
  - app.services.model_pool — resolves an abstract ModelChoice into either a
    LangChain client (for Hands) or a litellm model string (for Judge /
    self-execution / the classifier call itself).

Feature flag: reuses VISUAL_AUDIT_ENABLED (see app/api/v1/orchestrator.py) —
the orchestrator's only entry point today (AutonomousQASection.tsx) is
already gated by it. This means the orchestrator's Hands-only and
self-execute-only modes are also technically behind a flag named after
"visual audit" even when they never touch Judge — a deliberate
simplification, not a sign the flag means "Judge only".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Plain-language summary phrases shown directly to non-technical users —
# the raw sub-agent status ('failed', 'partial', etc.) is still available
# via run.status for anyone who wants it, but the summary text should read
# like an explanation, not a status dump.
_HANDS_PHRASES = {
    "passed": "The AI agent completed the requested actions successfully.",
    "failed": (
        "The AI agent could not complete the requested actions — open the "
        "linked test run below to see exactly which step failed."
    ),
    "inconclusive": (
        "The AI agent finished but couldn't confirm whether the goal was "
        "met — open the linked test run below to review what happened."
    ),
    "cancelled": "The AI agent run was cancelled before it finished.",
}

_JUDGE_PHRASES = {
    "passed": "The live page matches the design closely — no significant differences found.",
    "failed": (
        "The live page doesn't match the design — see the differences listed below."
    ),
    "partial": (
        "The pixel-level comparison finished, but the AI structural check "
        "was unavailable. The results below are based on pixel comparison only."
    ),
    "error": "The visual comparison could not be completed.",
    "cancelled": "The visual comparison was cancelled before it finished.",
}


def _hands_summary_phrase(status: Optional[str]) -> str:
    return _HANDS_PHRASES.get(status, f"The AI agent finished with status '{status}'.")


def _judge_summary_phrase(status: Optional[str]) -> str:
    return _JUDGE_PHRASES.get(status, f"The visual comparison finished with status '{status}'.")


_CLASSIFIER_SYSTEM = (
    "You are a routing coordinator for a QA automation platform. Given a "
    "task description, decide: (1) does it require live browser interaction "
    "(a headless agent that clicks/types/navigates a real website) - set "
    "needs_hands; (2) does it require comparing a live page against a "
    "design reference image - set needs_judge; (3) is the task simple "
    "enough for a small/free model, or does it need a more capable model - "
    "set tier to 'cheap' or 'capable'. Respond with JSON only: "
    '{"needs_hands": bool, "needs_judge": bool, "tier": "cheap"|"capable", '
    '"rationale": str (one sentence)}'
)


@dataclass
class RoutingDecision:
    """One row of the audit trail — exactly what the live UI cards render."""

    step: str  # "hands" | "judge" | "self_execute"
    invoked: bool  # False if this step was considered but skipped
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    rationale: str = ""
    is_deterministic: bool = True  # False if decided by the classifier LLM call


@dataclass
class RoutingPlan:
    needs_hands: bool
    needs_judge: bool
    decisions: list[RoutingDecision] = field(default_factory=list)
    classifier_model_used: Optional[str] = None
    hands_choice: Optional[object] = None  # model_pool.ModelChoice | None
    judge_choice: Optional[object] = None  # model_pool.ModelChoice | None
    self_execute_choice: Optional[object] = None  # model_pool.ModelChoice | None — only set when needs_hands and needs_judge are both False


def plan_run(
    goal: Optional[str],
    target_url: Optional[str],
    has_artifact: bool,
    has_video_artifact: bool,
    sensitive_data_present: bool,
) -> RoutingPlan:
    """Hybrid decision logic: deterministic rules resolve the obvious cases;
    only the genuinely ambiguous combination (url + goal + artifact all
    present) triggers one cheap classifier call. See module docstring.
    """
    from app.services import llm_router, model_pool

    goal = (goal or "").strip() or None
    has_url = bool(target_url)
    decisions: list[RoutingDecision] = []
    pool = model_pool.available_pool()

    # Video artifacts feed checkpoints (parsed via video_ingest.py's Gemini
    # Files API at upload time), not a design image the Judge can pixel-diff
    # against — hard constraint, no model choice, Judge is never applicable.
    # video_judge_skip_recorded guards the branches below from also
    # appending their own generic "no design reference" judge decision —
    # this one, more specific rationale is enough.
    video_judge_skip_recorded = has_video_artifact
    if has_video_artifact:
        decisions.append(
            RoutingDecision(
                step="judge",
                invoked=False,
                rationale=(
                    "Reference is a video artifact — its checkpoints were "
                    "already extracted via Gemini's Files API at upload "
                    "time. The Judge compares against a design image, not "
                    "a video, so it does not apply to this reference."
                ),
            )
        )
        has_artifact = False  # a video reference can't drive the image Judge

    # ── Deterministic branches ────────────────────────────────────────────

    if not has_url and not has_artifact and goal:
        # Pure text task — no sub-agent needed at all (mirrors
        # design_ingest.parse_sow()'s existing self-execution precedent).
        tier_choice = (
            model_pool.cheapest(pool) if len(goal) < 500 else model_pool.most_capable(pool)
        )
        decisions.append(
            RoutingDecision(
                step="self_execute",
                invoked=True,
                model_provider=tier_choice.provider if tier_choice else None,
                model_name=tier_choice.model if tier_choice else None,
                rationale="No URL or design reference given — pure text task, answered directly without a sub-agent.",
            )
        )
        decisions.append(
            RoutingDecision(step="hands", invoked=False, rationale="No URL given — no browser interaction needed.")
        )
        if not video_judge_skip_recorded:
            decisions.append(
                RoutingDecision(step="judge", invoked=False, rationale="No design reference given — nothing to visually compare.")
            )
        return RoutingPlan(needs_hands=False, needs_judge=False, decisions=decisions, self_execute_choice=tier_choice)

    if has_artifact and has_url and not goal:
        # Pure visual audit — matches today's default AutonomousQASection flow.
        choice = model_pool.most_capable(pool)
        decisions.append(
            RoutingDecision(
                step="judge",
                invoked=True,
                model_provider=choice.provider if choice else None,
                model_name=choice.model if choice else None,
                rationale="Design reference + URL, no goal — pure visual audit.",
            )
        )
        decisions.append(
            RoutingDecision(step="hands", invoked=False, rationale="No goal given — live browser interaction not needed for a visual-only audit.")
        )
        return RoutingPlan(needs_hands=False, needs_judge=True, decisions=decisions, judge_choice=choice)

    if has_url and goal and not has_artifact:
        # Prompt-only browser testing — the flexibility explicitly requested:
        # "testing by prompt only... no visual/design comparison at all".
        # Always most_capable(), never cheapest(), regardless of goal
        # length: unlike Judge/self-execute (one request), Hands drives a
        # live multi-step browser session — one LLM call per agent
        # decision, often a dozen+ in a row. A "cheap" tier free model
        # (e.g. OpenRouter's free tier) rate-limits hard under that request
        # pattern and can fail a run outright; a single classification/
        # text-answer call from it is fine, a whole agent session is not.
        choice = model_pool.most_capable(pool)
        decisions.append(
            RoutingDecision(
                step="hands",
                invoked=True,
                model_provider=choice.provider if choice else None,
                model_name=choice.model if choice else None,
                rationale="URL + goal, no design reference — prompt-only browser testing.",
            )
        )
        if not video_judge_skip_recorded:
            decisions.append(
                RoutingDecision(step="judge", invoked=False, rationale="No design reference given — nothing to visually compare.")
            )
        return RoutingPlan(needs_hands=True, needs_judge=False, decisions=decisions, hands_choice=choice)

    if has_url and has_artifact and goal:
        # Genuine ambiguity: could mean "do both independently" or "use the
        # goal to drive Hands, then have Judge verify against the reference
        # too". This is the one combination handed to the classifier.
        classifier_choice = model_pool.cheapest(pool)
        if classifier_choice is None:
            # Empty pool — never silently drop functionality.
            decisions.append(
                RoutingDecision(step="hands", invoked=True, rationale="No model pool configured — defaulting to running both sub-agents rather than guessing.")
            )
            decisions.append(
                RoutingDecision(step="judge", invoked=True, rationale="No model pool configured — defaulting to running both sub-agents rather than guessing.")
            )
            return RoutingPlan(needs_hands=True, needs_judge=True, decisions=decisions)

        try:
            result = llm_router.complete(
                f"Task description: {goal}\nHas live URL to test: yes\nHas design reference to compare against: yes",
                system=_CLASSIFIER_SYSTEM,
                expect_json=True,
                max_tokens=300,
                model_override=model_pool.to_litellm_model_string(classifier_choice),
            )
            parsed = result.parsed_json or {}
            needs_hands = bool(parsed.get("needs_hands", True))
            needs_judge = bool(parsed.get("needs_judge", True))
            tier = parsed.get("tier") if parsed.get("tier") in ("cheap", "capable") else "capable"
            rationale = str(
                parsed.get("rationale")
                or "Classifier determined the routing for this combined goal + reference task."
            )
            classifier_model_used = result.model_used
        except Exception as exc:  # noqa: BLE001 — classifier failure must never drop functionality
            logger.warning(
                "Orchestrator: classifier call failed, defaulting to running both sub-agents: %s", exc
            )
            needs_hands, needs_judge = True, True
            tier = "capable"
            rationale = f"Classifier unavailable ({exc}) — defaulted to running both sub-agents rather than silently skipping one."
            classifier_model_used = None

        tier_choice = model_pool.cheapest(pool) if tier == "cheap" else model_pool.most_capable(pool)
        hands_choice = tier_choice if needs_hands else None
        judge_choice = tier_choice if needs_judge else None

        decisions.append(
            RoutingDecision(
                step="hands",
                invoked=needs_hands,
                model_provider=hands_choice.provider if hands_choice else None,
                model_name=hands_choice.model if hands_choice else None,
                rationale=rationale,
                is_deterministic=False,
            )
        )
        decisions.append(
            RoutingDecision(
                step="judge",
                invoked=needs_judge,
                model_provider=judge_choice.provider if judge_choice else None,
                model_name=judge_choice.model if judge_choice else None,
                rationale=rationale,
                is_deterministic=False,
            )
        )
        return RoutingPlan(
            needs_hands=needs_hands,
            needs_judge=needs_judge,
            decisions=decisions,
            classifier_model_used=classifier_model_used,
            hands_choice=hands_choice,
            judge_choice=judge_choice,
        )

    # Nothing usable at all — the API layer rejects this before enqueueing,
    # so this is a defensive fallback, not an expected path.
    decisions.append(
        RoutingDecision(step="self_execute", invoked=False, rationale="No goal, URL, or design reference provided.")
    )
    return RoutingPlan(needs_hands=False, needs_judge=False, decisions=decisions)


# ── Execution ─────────────────────────────────────────────────────────────


def _run_hands(
    *,
    goal: str,
    target_url: Optional[str],
    environment: Optional[str],
    project_id,
    credential_profile_id,
    allowed_domains: Optional[list[str]],
    sensitive_data: Optional[dict],
    model_choice,
) -> tuple[str, str]:
    """Create a real AITestRun and execute it via ai_runner, reusing the
    exact same live-event-persistence machinery run_ai_test_task uses.
    Returns (AITestRun id, AITestRun terminal status) — the caller must
    inspect the status, since a successful *invocation* (no Python
    exception) is not the same as the goal actually being achieved; a rate
    limit, a missing element, or a timeout all complete normally here but
    land on status="failed"/"inconclusive"."""
    from app.core.database import SessionLocal
    from app.models.ai_runs import AICredentialProfile, AIRunStatus, AITestRun
    from app.services import ai_runner, model_pool
    from app.workers.tasks.ai_execution import (
        _make_live_event_sink,
        _maybe_save_skill,
        _upsert_ai_run_event,
    )

    session = SessionLocal()
    try:
        profile_name = None
        if credential_profile_id:
            profile = session.get(AICredentialProfile, credential_profile_id)
            profile_name = profile.name if profile else None

        run = AITestRun(
            goal=goal,
            environment=environment,
            project_id=project_id,
            credential_profile_id=credential_profile_id,
            credential_profile_name=profile_name,
            status=AIRunStatus.running,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        run_id = str(run.id)
    finally:
        session.close()

    llm_override = None
    if model_choice is not None:
        try:
            llm_override = model_pool.to_langchain_client(model_choice)
        except Exception as exc:  # noqa: BLE001 — fall back to ai_runner's own default precedence
            logger.warning("Orchestrator: could not build orchestrator-chosen client for Hands, falling back to default precedence: %s", exc)

    event_sink = _make_live_event_sink(run_id)
    result = ai_runner.run_ai_test_sync(
        goal=goal,
        environment_url=target_url or "about:blank",
        allowed_domains=allowed_domains,
        sensitive_data=sensitive_data,
        on_event=event_sink,
        llm_override=llm_override,
    )

    session = SessionLocal()
    try:
        run = session.get(AITestRun, run_id)
        if run is None:
            return run_id, result.get("status", "inconclusive")
        for event_data in result.get("events", []):
            _upsert_ai_run_event(session, run_id, event_data)

        completed_at = datetime.now(timezone.utc)
        started = run.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int((completed_at - started).total_seconds() * 1000) if started else None

        failing = result.get("failing_step")
        run.status = AIRunStatus(result["status"])
        run.completed_at = completed_at
        run.duration_ms = duration_ms
        run.step_count = len(result.get("events", []))
        run.summary = result.get("summary", "")
        if failing:
            run.failing_step_index = failing.get("sequence")
            run.failing_step_description = failing.get("description")
            run.failing_step_screenshot_url = failing.get("screenshot_url")
        session.commit()

        # Same auto-save-on-pass behavior as the plain New Test flow
        # (run_ai_test_task) — Hands runs invoked via the orchestrator were
        # missing this entirely, so a passing Autonomous QA run never became
        # a replayable skill.
        if run.status == AIRunStatus.passed:
            _maybe_save_skill(session, run, result.get("history_json"))
    finally:
        session.close()

    return run_id, result.get("status", "inconclusive")


def _run_judge(
    *,
    artifact_id: str,
    target_url: str,
    environment: Optional[str],
    project_id,
    model_choice,
) -> tuple[str, str]:
    """Create a real VisualRun and execute it via visual_judge, reusing the
    exact same screenshot-capture helper run_visual_audit_task uses.
    Returns (VisualRun id, VisualRun terminal status). Raises only for
    unrecoverable infrastructure failure (screenshot capture / judge() itself
    erroring) — the caller (execute_run) persists that onto the orchestrator
    run. A "failed" status (real design mismatches found) is a legitimate
    audit outcome, not a Python exception, so the caller must inspect it."""
    import os

    from app.core.database import SessionLocal
    from app.models.visual_qa import DesignArtifact, VisualFinding, VisualRun, VisualRunStatus
    from app.services import model_pool, visual_judge
    from app.workers.tasks.visual_audit import _capture_screenshot, data_dir

    session = SessionLocal()
    try:
        artifact = session.get(DesignArtifact, artifact_id)
        if artifact is None or not os.path.exists(artifact.storage_path):
            raise RuntimeError("Reference design artifact missing on disk.")

        run = VisualRun(
            project_id=project_id,
            environment=environment,
            target_url=target_url,
            artifact_id=artifact_id,
            status=VisualRunStatus.running,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        run_id = str(run.id)
        artifact_storage_path = artifact.storage_path
    finally:
        session.close()

    model_override = None
    if model_choice is not None:
        try:
            model_override = model_pool.to_litellm_model_string(model_choice)
        except Exception as exc:  # noqa: BLE001 — fall back to llm_router's own static chain
            logger.warning("Orchestrator: could not resolve orchestrator-chosen model for Judge, falling back to default chain: %s", exc)

    run_dir = os.path.join(data_dir(), "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    screenshot_path = os.path.join(run_dir, "screenshot.png")
    diff_path = os.path.join(run_dir, "diff.png")

    session = SessionLocal()
    try:
        run = session.get(VisualRun, run_id)
        try:
            _capture_screenshot(target_url, screenshot_path)
        except Exception as exc:  # noqa: BLE001
            run.status = VisualRunStatus.error
            run.error_message = f"Screenshot capture failed: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
            raise

        try:
            verdict = visual_judge.judge(
                artifact_storage_path, screenshot_path, diff_path, model_override=model_override
            )
        except Exception as exc:  # noqa: BLE001
            run.status = VisualRunStatus.error
            run.error_message = f"Judge failed: {exc}"
            run.screenshot_path = screenshot_path
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
            raise

        for f in verdict.findings:
            session.add(VisualFinding(run_id=run.id, **f))

        fail_pct = float(os.environ.get("VISUAL_FAIL_MISMATCH_PCT", 1.0))
        has_serious = any(f["severity"] in ("critical", "major") for f in verdict.findings)
        if verdict.pixel_mismatch_pct > fail_pct or has_serious:
            run.status = VisualRunStatus.failed
        elif not verdict.vision_available and not verdict.vision_skipped:
            run.status = VisualRunStatus.partial
        else:
            run.status = VisualRunStatus.passed

        run.screenshot_path = screenshot_path
        run.diff_image_path = verdict.diff_image_path
        run.pixel_mismatch_pct = int(round(verdict.pixel_mismatch_pct))
        run.summary = verdict.summary
        run.completed_at = datetime.now(timezone.utc)
        if run.started_at:
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
            run.duration_ms = int((run.completed_at - started).total_seconds() * 1000)
        status_value = run.status.value
        session.commit()
    finally:
        session.close()

    return run_id, status_value


def execute_run(run_id: str) -> None:
    """Called by run_orchestrator_task. Loads the OrchestratorRun, plans it,
    persists the routing decisions, then executes whichever of Hands/Judge/
    self-execute the plan calls for. Guarantees a terminal status on every
    exception path — mirrors run_visual_audit_task's try/except/finally shape.
    """
    from app.core.database import SessionLocal
    from app.models.ai_runs import AICredentialProfile
    from app.models.orchestrator import (
        OrchestratorRun,
        OrchestratorRunStatus,
        OrchestratorStepDecision,
    )
    from app.models.visual_qa import ArtifactType, DesignArtifact
    from app.services import llm_router, model_pool

    session = SessionLocal()
    try:
        run = session.get(OrchestratorRun, run_id)
        if run is None:
            logger.error("Orchestrator: run %s not found", run_id)
            return
        if run.status == OrchestratorRunStatus.cancelled:
            logger.info("Orchestrator: run %s cancelled before start", run_id)
            return

        run.status = OrchestratorRunStatus.planning
        run.started_at = datetime.now(timezone.utc)
        session.commit()

        # Capture scalar fields before closing the session for the
        # (potentially long-running) sub-agent execution below, matching
        # ai_execution.py's established close-before-heavy-work pattern.
        goal = run.goal
        target_url = run.target_url
        environment = run.environment
        project_id = run.project_id
        credential_profile_id = run.credential_profile_id
        artifact_id = run.artifact_id

        has_artifact = artifact_id is not None
        has_video_artifact = False
        if artifact_id:
            artifact = session.get(DesignArtifact, artifact_id)
            if artifact is None:
                run.status = OrchestratorRunStatus.error
                run.error_message = "Design reference artifact not found."
                run.completed_at = datetime.now(timezone.utc)
                session.commit()
                return
            has_video_artifact = artifact.artifact_type == ArtifactType.video

        allowed_domains: Optional[list[str]] = None
        sensitive_data: Optional[dict] = None
        if credential_profile_id:
            profile = session.get(AICredentialProfile, credential_profile_id)
            if profile:
                allowed_domains = profile.allowed_domains or []
                if profile.credentials_json:
                    try:
                        from app.services.credential_service import decrypt_credentials

                        sensitive_data = decrypt_credentials(profile.credentials_json)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Orchestrator: failed to decrypt credentials for profile %s: %s",
                            credential_profile_id,
                            exc,
                        )

        plan = plan_run(
            goal=goal,
            target_url=target_url,
            has_artifact=has_artifact,
            has_video_artifact=has_video_artifact,
            sensitive_data_present=sensitive_data is not None,
        )

        for i, decision in enumerate(plan.decisions):
            session.add(
                OrchestratorStepDecision(
                    run_id=run.id,
                    step=decision.step,
                    invoked=decision.invoked,
                    model_provider=decision.model_provider,
                    model_name=decision.model_name,
                    is_deterministic=decision.is_deterministic,
                    rationale=decision.rationale,
                    sequence=i,
                )
            )
        run.status = OrchestratorRunStatus.running
        session.commit()
    finally:
        session.close()

    # ── Execute the plan (no DB session held open during this) ────────────
    ai_test_run_id: Optional[str] = None
    visual_run_id: Optional[str] = None
    self_execute_answer: Optional[str] = None
    summary_parts: list[str] = []
    had_error = False
    error_message: Optional[str] = None
    # Sub-agent terminal statuses actually achieved (not just "invoked without
    # raising") — these drive the final OrchestratorRunStatus below. A
    # successful invocation whose goal was NOT achieved (rate limit, missing
    # element, real design mismatch) must not be reported as "passed".
    hands_status: Optional[str] = None
    judge_status: Optional[str] = None

    if plan.needs_hands:
        try:
            ai_test_run_id, hands_status = _run_hands(
                goal=goal or "",
                target_url=target_url,
                environment=environment,
                project_id=project_id,
                credential_profile_id=credential_profile_id,
                allowed_domains=allowed_domains,
                sensitive_data=sensitive_data,
                model_choice=plan.hands_choice,
            )
            summary_parts.append(_hands_summary_phrase(hands_status))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Orchestrator: Hands execution failed for run %s", run_id)
            had_error = True
            error_message = f"The AI agent hit an unexpected error and couldn't finish ({exc})."

    if plan.needs_judge and not had_error:
        try:
            visual_run_id, judge_status = _run_judge(
                artifact_id=str(artifact_id),
                target_url=target_url or "",
                environment=environment,
                project_id=project_id,
                model_choice=plan.judge_choice,
            )
            summary_parts.append(_judge_summary_phrase(judge_status))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Orchestrator: Judge execution failed for run %s", run_id)
            had_error = True
            error_message = f"The visual comparison hit an unexpected error and couldn't finish ({exc})."

    if not plan.needs_hands and not plan.needs_judge and not had_error:
        try:
            model_str = (
                model_pool.to_litellm_model_string(plan.self_execute_choice)
                if plan.self_execute_choice
                else None
            )
            result = llm_router.complete(goal or "", model_override=model_str)
            self_execute_answer = result.text
            summary_parts.append(f"Answered directly via {result.model_used} — no sub-agent needed.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Orchestrator: self-execution failed for run %s", run_id)
            had_error = True
            error_message = f"Couldn't generate an answer due to an unexpected error ({exc})."

    # Sub-agent statuses that mean "invoked but did not achieve the goal /
    # infrastructure problem" (as opposed to Judge's "failed" = a genuine,
    # successfully-detected design mismatch — a legitimate audit result).
    hands_unsuccessful = hands_status in ("failed", "inconclusive", "cancelled")
    judge_infra_error = judge_status == "error"
    judge_found_mismatch = judge_status == "failed"
    judge_partial = judge_status == "partial"

    # ── Persist the final result ───────────────────────────────────────────
    session = SessionLocal()
    try:
        run = session.get(OrchestratorRun, run_id)
        if run is None:
            return
        if run.status == OrchestratorRunStatus.cancelled:
            return

        run.ai_test_run_id = ai_test_run_id
        run.visual_run_id = visual_run_id
        run.self_execute_answer = self_execute_answer
        run.completed_at = datetime.now(timezone.utc)
        if run.started_at:
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
            run.duration_ms = int((run.completed_at - started).total_seconds() * 1000)

        if had_error or judge_infra_error:
            run.status = OrchestratorRunStatus.error
            run.error_message = error_message or "Judge could not complete the audit — see the linked visual run."
        elif hands_unsuccessful:
            run.status = OrchestratorRunStatus.failed
            run.error_message = None
            run.summary = " ".join(summary_parts)
        elif judge_found_mismatch:
            run.status = OrchestratorRunStatus.failed
            run.summary = " ".join(summary_parts)
        elif judge_partial:
            run.status = OrchestratorRunStatus.partial
            run.summary = " ".join(summary_parts)
        else:
            run.status = OrchestratorRunStatus.passed
            run.summary = " ".join(summary_parts) or "Run completed."
        session.commit()
        logger.info("Orchestrator: run %s finished with status %s", run_id, run.status.value)
    except Exception:
        logger.exception("Orchestrator: unexpected failure persisting result for run %s", run_id)
        session.rollback()
        try:
            run = session.get(OrchestratorRun, run_id)
            if run and run.status not in (
                OrchestratorRunStatus.passed,
                OrchestratorRunStatus.failed,
                OrchestratorRunStatus.error,
                OrchestratorRunStatus.cancelled,
            ):
                run.status = OrchestratorRunStatus.error
                run.error_message = "Unexpected worker failure — see worker logs."
                run.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Orchestrator: could not mark run %s as errored", run_id)
    finally:
        session.close()
