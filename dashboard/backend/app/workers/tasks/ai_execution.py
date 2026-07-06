"""Celery task — execute an AI test run and persist events to the database."""
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _upsert_ai_run_event(session, run_id: str, event_data: dict) -> None:
    """Insert or update a single AIRunEvent row, keyed by (run_id, sequence).

    Used both for live streaming during execution (one throwaway session per
    call, via _make_live_event_sink) and for the post-run reconciliation pass
    below, which fills in any event that failed to persist live (e.g. a
    transient DB hiccup) without creating duplicate rows.
    """
    from app.models.ai_runs import AIRunEvent

    existing = (
        session.query(AIRunEvent)
        .filter(AIRunEvent.run_id == run_id, AIRunEvent.sequence == event_data["sequence"])
        .one_or_none()
    )
    fields = dict(
        status=event_data["status"],
        description=event_data["description"],
        step_type=event_data.get("step_type", "deterministic"),
        elapsed_ms=event_data.get("elapsed_ms"),
        screenshot_url=event_data.get("screenshot_url"),
        highlighted_element=event_data.get("highlighted_element"),
        is_failing_step=event_data.get("is_failing_step", False),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        session.add(AIRunEvent(run_id=run_id, sequence=event_data["sequence"], **fields))


def _make_live_event_sink(run_id: str):
    """Build a callback that persists one AI run event immediately.

    The SSE endpoint (GET /ai-testing/runs/{run_id}/stream) polls the DB
    every 500ms for events with sequence > last_seen. Previously all events
    were written in one batch after the whole run finished, so the "live"
    stream had nothing to show until the very end. This writes each event
    as soon as it happens instead, using a short-lived session per call so
    we don't hold a DB connection open for the whole (potentially long)
    browser automation.
    """

    def _sink(event_data: dict) -> None:
        from sqlalchemy import func

        from app.core.database import SessionLocal
        from app.models.ai_runs import AIRunEvent, AITestRun

        session = SessionLocal()
        try:
            _upsert_ai_run_event(session, run_id, event_data)
            run = session.get(AITestRun, run_id)
            if run is not None:
                max_seq = (
                    session.query(func.max(AIRunEvent.sequence))
                    .filter(AIRunEvent.run_id == run_id)
                    .scalar()
                    or 0
                )
                run.step_count = max_seq
            session.commit()
        except Exception:
            logger.exception(
                "Failed to persist live AI run event (run_id=%s, sequence=%s)",
                run_id,
                event_data.get("sequence"),
            )
            session.rollback()
        finally:
            session.close()

    return _sink


def _resolve_run_inputs(db, run) -> tuple[str, list | None, dict | None]:
    """Resolve (environment_url, allowed_domains, sensitive_data) for a run.

    Shared by run_ai_test_task and replay_skill_task."""
    from app.models.ai_runs import AICredentialProfile

    environment_url = "about:blank"
    if run.project_id:
        from app.models.project import Project  # noqa: F401
        # Project model has no base_url — environment_url stays as placeholder.
        # The AI agent navigates to the correct URL from the goal text.

    allowed_domains: list[str] | None = None
    sensitive_data: dict | None = None
    if run.credential_profile_id:
        profile = db.get(AICredentialProfile, run.credential_profile_id)
        if profile:
            allowed_domains = profile.allowed_domains or []
            if profile.credentials_json:
                try:
                    from app.services.credential_service import decrypt_credentials
                    sensitive_data = decrypt_credentials(profile.credentials_json)
                except Exception as exc:
                    logger.warning(
                        "Failed to decrypt credentials for profile %s: %s",
                        run.credential_profile_id,
                        exc,
                    )
    return environment_url, allowed_domains, sensitive_data


def _persist_result(db, run, run_id: str, result: dict) -> None:
    """Persist a finished run result (events, status, timing, summaries).

    Generates the LLM narrative summary here (single post-run call); if it
    fails the raw engine summary is kept — the run is never blocked on it."""
    from app.models.ai_runs import AIRunStatus

    # Reconcile events: already streamed live via event_sink; this fills any
    # that failed to persist live without duplicating rows.
    for event_data in result.get("events", []):
        _upsert_ai_run_event(db, run_id, event_data)

    completed_at = datetime.now(timezone.utc)
    started = (
        run.started_at.replace(tzinfo=timezone.utc)
        if run.started_at and run.started_at.tzinfo is None
        else run.started_at
    )
    duration_ms = (
        int((completed_at - started).total_seconds() * 1000) if started else None
    )

    raw_summary = result.get("summary", "")
    narrative = None
    try:
        from app.services.ai_runner import generate_narrative_summary
        narrative = generate_narrative_summary(
            goal=run.goal,
            status=result["status"],
            events=result.get("events", []),
            raw_summary=raw_summary,
        )
    except Exception:
        logger.exception("Narrative summary generation raised; keeping raw summary")

    failing = result.get("failing_step")
    run.status = AIRunStatus(result["status"])
    run.completed_at = completed_at
    run.duration_ms = duration_ms
    run.step_count = len(result.get("events", []))
    run.summary = narrative or raw_summary
    run.raw_summary = raw_summary
    if failing:
        run.failing_step_index = failing.get("sequence")
        run.failing_step_description = failing.get("description")
        run.failing_step_screenshot_url = failing.get("screenshot_url")


def _resolve_hands_llm_override(
    goal: str, environment_url: str, sensitive_data: dict | None
):
    """Ask the orchestrator ("the Brain") which model should drive Hands for
    this goal, via the same model_pool cheap/capable selection (including
    OpenRouter) the Autonomous QA pipeline already uses for the identical
    "goal + URL, no design reference" case — instead of leaving model
    choice to ai_runner's own static Anthropic->OpenAI->Google precedence.

    Returns None on any failure (including "no model in the pool"), so the
    caller falls back to ai_runner.run_ai_test_sync()'s default precedence
    unchanged — unifying model selection must never be able to block a
    test run just because the orchestrator's own selection logic hiccups."""
    try:
        from app.services import model_pool
        from app.services.orchestrator import plan_run

        plan = plan_run(
            goal=goal,
            target_url=environment_url,
            has_artifact=False,
            has_video_artifact=False,
            sensitive_data_present=sensitive_data is not None,
        )
        if plan.hands_choice is None:
            return None
        return model_pool.to_langchain_client(plan.hands_choice)
    except Exception as exc:
        logger.warning(
            "Orchestrator model selection failed for Hands, falling back to "
            "ai_runner's default precedence: %s",
            exc,
        )
        return None


def _goal_hash(goal: str) -> str:
    import hashlib
    normalized = " ".join(goal.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _maybe_save_skill(db, run, history_json: str | None) -> None:
    """Auto-save (or refresh) a skill after a passed AI-planned run.

    Upserts by goal_hash — the latest passing run's history wins. Replay
    runs never create or overwrite skills. Any failure here is logged and
    swallowed: skill capture must never fail run persistence."""
    from app.models.ai_runs import AISkill

    try:
        if not history_json:
            return
        if (run.run_type or "ai") != "ai":
            return

        goal_hash = _goal_hash(run.goal)
        step_count = run.step_count or 0
        skill = db.query(AISkill).filter(AISkill.goal_hash == goal_hash).one_or_none()
        if skill is not None:
            skill.history_json = history_json
            skill.step_count = step_count
            skill.source_run_id = run.id
            skill.environment = run.environment
            skill.credential_profile_id = run.credential_profile_id
            skill.project_id = run.project_id
        else:
            name = " ".join(run.goal.split())
            if len(name) > 120:
                name = name[:117] + "..."
            skill = AISkill(
                name=name,
                goal=run.goal,
                goal_hash=goal_hash,
                source_run_id=run.id,
                project_id=run.project_id,
                environment=run.environment,
                credential_profile_id=run.credential_profile_id,
                history_json=history_json,
                step_count=step_count,
                created_by=run.created_by,
            )
            db.add(skill)
        db.commit()
        logger.info("Skill saved/refreshed for run %s (goal_hash=%s)", run.id, goal_hash)
    except Exception:
        logger.exception("Skill capture failed for run %s (run persisted normally)", run.id)
        db.rollback()


@celery_app.task(
    name="ai_execution.run_ai_test_task",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_ai_test_task(self, run_id: str) -> None:
    """Execute an AI test run identified by run_id."""
    from app.core.database import SessionLocal
    from app.models.ai_runs import AIRunStatus, AITestRun

    db = SessionLocal()
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            logger.error("AI run %s not found in DB", run_id)
            return

        # Abort if already cancelled (client cancelled before task started)
        if run.status == AIRunStatus.cancelled:
            logger.info("AI run %s was cancelled before execution started", run_id)
            return

        run.status = AIRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        environment_url, allowed_domains, sensitive_data = _resolve_run_inputs(db, run)
        llm_override = _resolve_hands_llm_override(run.goal, environment_url, sensitive_data)

        event_sink = _make_live_event_sink(run_id)

        db.close()
        db = None

        # Run execution engine (synchronous wrapper around async playwright/browser-use)
        from app.services.ai_runner import run_ai_test_sync

        result = run_ai_test_sync(
            goal=run.goal,
            environment_url=environment_url,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            on_event=event_sink,
            llm_override=llm_override,
        )

        # Re-open DB to persist results
        db = SessionLocal()
        run = db.get(AITestRun, run_id)
        if run is None:
            return

        # Check if cancelled while running
        if run.status == AIRunStatus.cancelled:
            logger.info("AI run %s was cancelled during execution", run_id)
            return

        _persist_result(db, run, run_id, result)
        db.commit()
        logger.info("AI run %s completed with status: %s", run_id, result["status"])

        # Auto-save a replayable skill from passed AI-planned runs.
        if result["status"] == "passed":
            _maybe_save_skill(db, run, result.get("history_json"))

    except Exception as exc:
        logger.exception("AI run %s raised an unhandled exception: %s", run_id, exc)
        if db:
            try:
                from app.models.ai_runs import AIRunStatus, AITestRun
                run = db.get(AITestRun, run_id)
                if run and run.status == AIRunStatus.running:
                    run.status = AIRunStatus.inconclusive
                    run.summary = f"Unhandled execution error: {exc}"
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                pass
    finally:
        if db:
            db.close()


@celery_app.task(
    name="ai_execution.replay_skill_task",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def replay_skill_task(self, run_id: str, skill_id: str, allow_ai_fallback: bool = False) -> None:
    """Replay a saved skill's recorded actions as a normal AI test run.

    The run row (run_type="skill_replay") was already created by the API;
    events stream through the same live sink / SSE as AI-planned runs.
    A failed replay marks the run failed — no silent AI fallback unless
    allow_ai_fallback was explicitly requested."""
    from app.core.database import SessionLocal
    from app.models.ai_runs import AIRunStatus, AISkill, AITestRun

    db = SessionLocal()
    try:
        run = db.get(AITestRun, run_id)
        if run is None:
            logger.error("Skill replay run %s not found in DB", run_id)
            return
        if run.status == AIRunStatus.cancelled:
            logger.info("Skill replay run %s was cancelled before execution", run_id)
            return

        skill = db.get(AISkill, skill_id)
        if skill is None or not skill.history_json:
            run.status = AIRunStatus.failed
            run.summary = "Skill no longer exists or has no recorded actions."
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        run.status = AIRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        environment_url, allowed_domains, sensitive_data = _resolve_run_inputs(db, run)
        history_json = skill.history_json
        goal = run.goal
        event_sink = _make_live_event_sink(run_id)

        db.close()
        db = None

        from app.services.ai_runner import run_skill_replay_sync

        result = run_skill_replay_sync(
            goal=goal,
            history_json=history_json,
            environment_url=environment_url,
            allowed_domains=allowed_domains,
            sensitive_data=sensitive_data,
            on_event=event_sink,
            allow_ai_fallback=allow_ai_fallback,
        )

        db = SessionLocal()
        run = db.get(AITestRun, run_id)
        if run is None:
            return
        if run.status == AIRunStatus.cancelled:
            logger.info("Skill replay run %s was cancelled during execution", run_id)
            return

        _persist_result(db, run, run_id, result)

        skill = db.get(AISkill, skill_id)
        if skill is not None:
            skill.times_replayed = (skill.times_replayed or 0) + 1
            skill.last_replay_status = result["status"]
            skill.last_replayed_at = datetime.now(timezone.utc)

        db.commit()
        logger.info("Skill replay %s completed with status: %s", run_id, result["status"])

    except Exception as exc:
        logger.exception("Skill replay %s raised an unhandled exception: %s", run_id, exc)
        if db:
            try:
                from app.models.ai_runs import AIRunStatus, AITestRun
                run = db.get(AITestRun, run_id)
                if run and run.status == AIRunStatus.running:
                    run.status = AIRunStatus.inconclusive
                    run.summary = f"Unhandled execution error: {exc}"
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                pass
    finally:
        if db:
            db.close()
