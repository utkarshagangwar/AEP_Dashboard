"""Celery task — execute an AI test run and persist events to the database."""
import os
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


def _resolve_bypass_profile(profile) -> tuple[str, list[dict]]:
    """Resolve a kind="bypass" credential profile into (target_url, cookies).

    Calls the target app's admin API-key login endpoint directly (plain HTTP
    — mirrors the same simple POST-then-inject-cookie pattern the
    ig_automation Robot Framework suite uses for its own tests, reimplemented
    natively here; this module does not import or depend on that suite) and
    turns the returned token into a Playwright-shaped cookie the AI runner
    injects before navigating, so the agent starts already authenticated and
    never has to fight a CAPTCHA-gated login form.

    The X-API-Key header alone is sufficient — confirmed against the actual
    endpoint behavior, it grants access directly from the key with no
    email/otp identity required. (ig_automation's hopscotch_client.py also
    sends email/otp in its POST body, but that's specific to its own flow —
    not something this endpoint requires. Do not reintroduce it here.)

    Raises on any failure (missing config, network error, non-2xx response,
    missing auth_token) — deliberately not swallowed. The caller (inside
    run_ai_test_task/replay_skill_task, before the DB session is closed) lets
    this propagate to the existing outer exception handler, which marks the
    run inconclusive with the exception message. This keeps a doomed run from
    ever launching Chromium.
    """
    import requests

    from app.services.credential_service import decrypt_credentials

    if not profile.credentials_json:
        raise ValueError(f"Bypass profile '{profile.name}' has no stored credentials")
    creds = decrypt_credentials(profile.credentials_json)

    api_base_url = (creds.get("api_base_url") or "").rstrip("/")
    bypass_endpoint = creds.get("bypass_endpoint") or "/admin-login-by-api-key"
    api_key = creds.get("api_key")
    cookie_name = creds.get("cookie_name") or "authToken"
    cookie_domain = creds.get("cookie_domain")

    if not api_base_url or not api_key or not cookie_domain:
        raise ValueError(
            f"Bypass profile '{profile.name}' is missing api_base_url/api_key/cookie_domain"
        )
    if not profile.target_url:
        raise ValueError(f"Bypass profile '{profile.name}' has no target_url")

    resp = requests.post(
        f"{api_base_url}{bypass_endpoint}",
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    auth_token = resp.json().get("auth_token")
    if not auth_token:
        raise ValueError(
            f"Bypass login for '{profile.name}' returned no auth_token"
        )

    cookies = [
        {"name": cookie_name, "value": auth_token, "domain": cookie_domain, "path": "/"}
    ]
    return profile.target_url, cookies


def _resolve_run_inputs(
    db, run
) -> tuple[str, list | None, dict | None, list[dict] | None]:
    """Resolve (environment_url, allowed_domains, sensitive_data, cookies) for a run.

    Shared by run_ai_test_task and replay_skill_task.

    Three mutually-exclusive sources, in priority order:
    1. A saved credential_profile_id — kind="bypass" resolves to (target_url,
       cookies) via _resolve_bypass_profile(); any other kind (including
       null/"standard") decrypts credentials into sensitive_data as before.
    2. An ad-hoc target_url/login on the run itself (the one-off "Website
       without/with login" path — never a saved profile).
    3. Neither — environment_url stays "about:blank", the AI agent navigates
       from the goal text, exactly as today.
    """
    from app.models.ai_runs import AICredentialProfile

    environment_url = "about:blank"
    allowed_domains: list[str] | None = None
    sensitive_data: dict | None = None
    cookies: list[dict] | None = None

    if run.credential_profile_id:
        profile = db.get(AICredentialProfile, run.credential_profile_id)
        if profile:
            allowed_domains = profile.allowed_domains or []
            if (profile.kind or "standard") == "bypass":
                environment_url, cookies = _resolve_bypass_profile(profile)
            elif profile.credentials_json:
                try:
                    from app.services.credential_service import decrypt_credentials
                    sensitive_data = decrypt_credentials(profile.credentials_json)
                except Exception as exc:
                    logger.warning(
                        "Failed to decrypt credentials for profile %s: %s",
                        run.credential_profile_id,
                        exc,
                    )
    elif getattr(run, "adhoc_target_url", None):
        environment_url = run.adhoc_target_url
        if getattr(run, "adhoc_credentials_json", None):
            from app.services.credential_service import decrypt_credentials
            from urllib.parse import urlparse

            try:
                sensitive_data = decrypt_credentials(run.adhoc_credentials_json)
            except Exception as exc:
                logger.warning(
                    "Failed to decrypt ad-hoc credentials for run %s: %s", run.id, exc
                )
            host = urlparse(run.adhoc_target_url).hostname
            allowed_domains = [host] if host else []

    return environment_url, allowed_domains, sensitive_data, cookies


def _resolve_android_credential(db, run) -> dict | None:
    """Resolve run.credential_profile_id for an Android run into a plain
    {field: value} dict android_runner.py can substitute into <cred:...>
    placeholders.

    Raises if the profile is kind="bypass" — that mechanism injects a
    Playwright browser cookie (see _resolve_bypass_profile) and has no
    Android counterpart yet. The exception propagates to run_ai_test_task's
    outer except Exception handler, which already marks the run
    inconclusive with the message — the same "raise on any failure, let the
    outer handler catch it" convention _resolve_bypass_profile itself uses.
    """
    from app.models.ai_runs import AICredentialProfile

    if not run.credential_profile_id:
        return None
    profile = db.get(AICredentialProfile, run.credential_profile_id)
    if profile is None:
        return None
    if (profile.kind or "standard") == "bypass":
        raise ValueError(
            f"Credential profile '{profile.name}' is a bypass profile — "
            "bypass login is not supported for Android runs yet."
        )
    if not profile.credentials_json:
        return None
    from app.services.credential_service import decrypt_credentials

    return decrypt_credentials(profile.credentials_json)


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
    # Android-only: {farm_vendor, farm_session_id, dashboard_url, video_url}.
    # Always absent from a web result dict, so this is a no-op for web runs.
    if "platform_metadata" in result:
        run.platform_metadata = result.get("platform_metadata")
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
    from app.services.skill_store import compute_goal_hash
    return compute_goal_hash(goal)


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


def _maybe_bump_skill_stats(db, run, status: str) -> None:
    """Update replay bookkeeping on the originating skill, if any. Shared by
    a deterministic replay and by a fresh AI-planned run started by clicking
    Replay/Run on a prompt-only skill — both set run.skill_id."""
    if not run.skill_id:
        return
    from app.models.ai_runs import AISkill

    skill = db.get(AISkill, run.skill_id)
    if skill is not None:
        skill.times_replayed = (skill.times_replayed or 0) + 1
        skill.last_replay_status = status
        skill.last_replayed_at = datetime.now(timezone.utc)


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

        if (run.platform or "web") == "android":
            from app.models.ai_runs import AndroidAppBuild

            build = (
                db.get(AndroidAppBuild, run.android_app_build_id)
                if run.android_app_build_id
                else None
            )
            if build is None:
                run.status = AIRunStatus.inconclusive
                run.summary = "Android app build not found."
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            sensitive_data = _resolve_android_credential(db, run)
            event_sink = _make_live_event_sink(run_id)
            farm_app_id = build.farm_app_id
            device_profile = run.device_profile

            db.close()
            db = None

            from app.services.android_runner import run_android_test_sync

            result = run_android_test_sync(
                goal=run.goal,
                farm_app_id=farm_app_id,
                device_profile=device_profile,
                sensitive_data=sensitive_data,
                on_event=event_sink,
                max_steps=int(os.environ.get("ANDROID_MAX_STEPS", "25")),
                max_duration_s=int(os.environ.get("ANDROID_MAX_DURATION_S", "480")),
            )
        else:
            environment_url, allowed_domains, sensitive_data, cookies = _resolve_run_inputs(db, run)
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
                cookies=cookies,
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

        # If this run started from clicking Replay/Run on a skill (prompt
        # skill with no recording yet), keep its replay bookkeeping current.
        if run.skill_id:
            _maybe_bump_skill_stats(db, run, result["status"])
            db.commit()

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

        environment_url, allowed_domains, sensitive_data, cookies = _resolve_run_inputs(db, run)
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
            cookies=cookies,
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
        _maybe_bump_skill_stats(db, run, result["status"])
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
