"""Celery task — execute an AI test run and persist events to the database."""
import os
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _positive_env_int(name: str, default: int) -> int:
    """Read a positive integer setting without making a bad deployment env
    value prevent the worker from importing."""
    try:
        value = int(os.environ.get(name, default))
        return value if value > 0 else default
    except ValueError:
        return default

# Third-party identity providers a "log in as X" goal can legitimately land
# on mid-flow (an SSO button on the target app redirecting/popping up to the
# provider's own real login page) before returning control to the target
# app. browser-use's BrowserContext._check_and_handle_navigation blocks any
# top-level navigation whose hostname isn't in allowed_domains — a
# necessary guard against the agent (and any sensitive_data it's holding)
# wandering to an out-of-scope site, but it has no notion of "this
# particular external domain is just the login step of the site I was told
# to test." Without this list, any goal whose target app authenticates via
# Google/Microsoft/etc. SSO fails deterministically the moment the OAuth
# popup opens, with an opaque "blocked by security restrictions" error
# surfaced only after the run completes.
#
# Matching is apex-domain suffix match (see browser-use's _is_url_allowed:
# `domain == allowed_domain or domain.endswith('.' + allowed_domain)`), so
# listing the apex ("google.com") also covers every subdomain the OAuth
# flow actually uses (accounts.google.com, myaccount.google.com, etc.)
# without needing to enumerate them.
#
# This does NOT guarantee a successful login — Google and other providers
# run their own bot/automation detection independent of anything on our
# side ("This browser or app may not be secure") and may still refuse an
# automated sign-in. It only removes the guaranteed, always-fails block our
# own allowlist was causing; a provider-side block after this still
# surfaces as a normal run failure with its own message.
# New Vibe Test goals are often long, real multi-page workflows (e.g. log
# in, create a record through a multi-step form, then act on that record),
# not the short one-shot actions the previous 30-step/600s ceiling was tuned
# for -- that ceiling was cutting off legitimate large runs before they
# could finish. browser_use.Agent.run() requires a real int (there's no
# "unlimited" sentinel), so a very large step count stands in for "run until
# the agent finishes" here; max_duration_s=None removes the wall-clock
# timeout entirely (ai_runner.resolve_with_ai treats timeout=None as "wait
# forever"). Scoped to this call site only -- ai_runner.py's own defaults
# (30 steps / 600s) are untouched, so every other caller (orchestrator.py's
# Autonomous QA "Hands" step, skill-replay's AI fallback) keeps today's
# behavior.
_VIBE_TEST_MAX_STEPS = 100_000

# The agent itself intentionally has no short wall-clock ceiling, but the
# worker formerly retained Celery's global 30-minute soft / 60-minute hard
# limits.  Consequently a legitimate long run was interrupted regardless of
# its agent settings.  Give this task family its own bounded, configurable
# budget and leave every other Celery task on the existing global limits.
_VIBE_TEST_SOFT_TIME_LIMIT_S = _positive_env_int(
    "VIBE_TEST_SOFT_TIME_LIMIT_S", 21_600
)
_VIBE_TEST_HARD_TIME_LIMIT_S = max(
    _positive_env_int("VIBE_TEST_HARD_TIME_LIMIT_S", _VIBE_TEST_SOFT_TIME_LIMIT_S + 300),
    _VIBE_TEST_SOFT_TIME_LIMIT_S + 60,
)

_OAUTH_PROVIDER_DOMAINS: list[str] = [
    "google.com",  # accounts.google.com, myaccount.google.com
    "microsoftonline.com",  # login.microsoftonline.com (Azure AD / Entra ID)
    "live.com",  # login.live.com (Microsoft personal accounts)
    "microsoft.com",  # login.microsoft.com
    "appleid.apple.com",
    "apple.com",
    "github.com",  # GitHub OAuth (github.com/login/oauth/...)
    "okta.com",  # tenant subdomains, e.g. yourcompany.okta.com
    "auth0.com",  # tenant subdomains
    "facebook.com",
]


def _widen_allowed_domains_for_sso(
    allowed_domains: list[str] | None,
) -> list[str] | None:
    """Merge the known-OAuth-provider allowlist into a run's allowed_domains.

    A no-op when allowed_domains is None/empty — that already means
    "no domain restriction" to browser-use (see _is_url_allowed: an empty/
    falsy allowed_domains short-circuits to allow everything), so there's
    nothing to widen and doing so would incorrectly turn an unrestricted run
    into a restricted one.
    """
    if not allowed_domains:
        return allowed_domains
    merged = list(allowed_domains)
    for domain in _OAUTH_PROVIDER_DOMAINS:
        if domain not in merged:
            merged.append(domain)
    return merged


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


def _persist_video_path(run_id: str, video_path: str) -> None:
    """Persist the finished live-capture recording's path onto its run.

    Called as ai_runner.py's on_video_ready callback, from inside the async
    execution flow — same "short-lived session per call" pattern as
    _make_live_event_sink above, so we don't hold a DB connection open for
    the whole (potentially long) browser automation. Best-effort: a failure
    here must never fail the run itself (it has already completed by the
    time this fires)."""
    from app.core.database import SessionLocal
    from app.models.ai_runs import AITestRun

    session = SessionLocal()
    try:
        run = session.get(AITestRun, run_id)
        if run is not None:
            run.video_path = video_path
            session.commit()
    except Exception:
        logger.exception("Failed to persist video path for run %s", run_id)
        session.rollback()
    finally:
        session.close()


def _resolve_bypass_profile(profile) -> tuple[str, list[dict]]:
    """Resolve a kind="bypass" credential profile into (target_url, cookies).

    Calls the target app's admin API-key login endpoint directly (plain HTTP)
    and turns the returned token into a Playwright-shaped cookie the AI
    runner injects before navigating, so the agent starts already
    authenticated and never has to fight a CAPTCHA-gated login form.

    GET, reading "token" from the response — matches the endpoint contract
    ig_automation's hopscotch_client.py confirmed with the dev on 2026-07-23
    (GET /auth/admin-token, {"token": ...}), which replaced the old
    POST /admin-login-by-api-key + {"auth_token": ...} contract this
    function originally targeted. Updated here to match; credential
    profiles must be re-pointed at the new API_BASE_URL/BYPASS_ENDPOINT
    (see ig_automation/.env) — old profiles built for the retired POST
    endpoint will fail here the same way they'd fail with any client.

    The X-API-Key header alone is sufficient — confirmed against the actual
    endpoint behavior, it grants access directly from the key with no
    email/otp identity required. (ig_automation's hopscotch_client.py sends
    x-api-key too but no email/otp on the wire — the token is scoped to the
    key itself. Do not add an email/otp param here.)

    Raises on any failure (missing config, network error, non-2xx response,
    missing token) — deliberately not swallowed. The caller (inside
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

    resp = requests.get(
        f"{api_base_url}{bypass_endpoint}",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    auth_token = resp.json().get("token")
    if not auth_token:
        raise ValueError(
            f"Bypass login for '{profile.name}' returned no token"
        )

    cookies = [
        {"name": cookie_name, "value": auth_token, "domain": cookie_domain, "path": "/"}
    ]
    return profile.target_url, cookies


def _resolve_run_inputs(
    db, run
) -> tuple[str, list | None, dict | None, list[dict] | None, bool]:
    """Resolve (environment_url, allowed_domains, sensitive_data, cookies,
    unrestricted_domains) for a run.

    Shared by run_ai_test_task and replay_skill_task.

    Four sources, in priority order:
    1. A saved credential_profile_id — kind="bypass" resolves to (target_url,
       cookies) via _resolve_bypass_profile(); any other kind (including
       null/"standard") decrypts credentials into sensitive_data as before,
       AND now also contributes its target_url as the start URL. That last
       part is a fix, not a refinement: target_url was previously read only
       inside the bypass branch, so a standard profile supplied credentials
       with no address, environment_url stayed "about:blank", ai_runner's
       `!= "about:blank"` guard skipped page.goto() and the agent opened a
       blank tab.
       allowed_domains stays scoped to the profile's configured domains
       (plus the OAuth-provider widen below) — saved profiles keep the
       mandatory domain guardrail.
    2. An ad-hoc target_url/login on the run itself (the one-off "Website
       without/with login" path — never a saved profile). No allowed_domains
       is computed for this path at all (see unrestricted_domains below) —
       a deliberate, user-requested trade-off (2026-07-21): typed ad-hoc
       credentials commonly need to follow an internal SSO/enterprise-auth
       redirect to a *different subdomain or host entirely* (e.g.
       app.company.com's login button bouncing to
       intranet.company.com/enterprise/sso/...), which a same-host
       allowlist — or even the OAuth-provider widen below, which only
       covers known third-party IdPs — can't anticipate. The trade-off:
       these runs have no domain guardrail at all, so a malicious/
       compromised target page could in principle induce the agent to
       submit the typed credentials to an attacker-controlled domain.
    3. The run's project + environment, via the project_environments table
       (app.models.project.ProjectEnvironment, migration 0041) — base_url
       and an optional default credential profile. Consulted ONLY when 1
       and 2 produced no URL, so it cannot change the behaviour of any run
       that already resolved one. This is the path a SOW-extracted prompt
       skill takes: saved under a project, but never given a credential
       profile at parse time, because parsing knows nothing about
       environments or logins.
    4. None of the above — environment_url stays "about:blank" and the AI
       agent navigates from the goal text, exactly as before. Runs whose
       goal carries no URL are now rejected at submit time instead of
       reaching this state (see app/api/v1/ai_runs.py), so this branch is
       reserved for goals that genuinely embed their own address.
    """
    from app.models.ai_runs import AICredentialProfile
    from app.services.start_context import (
        NO_NAVIGATION_URL,
        derive_allowed_domains,
        resolve_start_context,
    )

    environment_url = NO_NAVIGATION_URL
    allowed_domains: list[str] | None = None
    sensitive_data: dict | None = None
    cookies: list[dict] | None = None
    unrestricted_domains = False

    if run.credential_profile_id:
        profile = db.get(AICredentialProfile, run.credential_profile_id)
        if profile:
            allowed_domains = profile.allowed_domains or []
            if (profile.kind or "standard") == "bypass":
                environment_url, cookies = _resolve_bypass_profile(profile)
            else:
                # A standard profile's target_url used to be ignored
                # outright — the profile handed over a password with
                # nowhere to type it, environment_url stayed
                # "about:blank", ai_runner skipped page.goto(), and the
                # agent reported a blank page. target_url is now honoured
                # for every kind, not just "bypass".
                if profile.target_url:
                    environment_url = profile.target_url
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
    elif getattr(run, "adhoc_target_url", None):
        environment_url = run.adhoc_target_url
        if getattr(run, "adhoc_credentials_json", None):
            from app.services.credential_service import decrypt_credentials

            try:
                sensitive_data = decrypt_credentials(run.adhoc_credentials_json)
            except Exception as exc:
                logger.warning(
                    "Failed to decrypt ad-hoc credentials for run %s: %s", run.id, exc
                )
            # No allowed_domains for ad-hoc runs — see docstring. Leaving
            # allowed_domains as None here (rather than [host]) means
            # browser-use's own navigation guard is fully open for this
            # run; unrestricted_domains=True tells ai_runner to skip the
            # "allowed_domains required when sensitive_data is set" gate
            # too, instead of raising.
            unrestricted_domains = True

    # ── Project-level fallback ──────────────────────────────────────────
    #
    # Runs when the branches above left the run without a login, a URL,
    # or both. Deliberately NOT gated on environment_url still being
    # "about:blank": the login and the URL are independent, and gating
    # the login on a missing URL is exactly the bug this replaces.
    #
    # The failure it caused: a SOW-extracted skill whose goal text
    # happened to mention its own address resolved a URL, so the login
    # lookup was skipped, so the project's kind="bypass" profile was
    # never loaded and no auth cookie was injected. The agent navigated
    # to the app unauthenticated, was bounced to the real login form, and
    # hit the reCAPTCHA the bypass profile exists to route around —
    # reproduced against dev.interviewgod.ai, where the action log
    # contained no "Inject authenticated session cookie" step at all.
    #
    # Still guarded on the ad-hoc path being untaken (unrestricted_
    # domains): an ad-hoc run has deliberately opted out of domain
    # allowlisting and must not silently acquire a project's saved
    # credentials.
    needs_login = not run.credential_profile_id and sensitive_data is None
    needs_url = environment_url == NO_NAVIGATION_URL
    if (needs_login or needs_url) and not unrestricted_domains:
        ctx = resolve_start_context(
            db,
            project_id=getattr(run, "project_id", None),
            environment=getattr(run, "environment", None),
            # Explicit profile/ad-hoc were already handled above; ask the
            # resolver for the project-level answer only, so this can
            # never override a choice the run already made.
            credential_profile_id=None,
            adhoc_target_url=None,
            goal=getattr(run, "goal", None),
        )

        # Adopt the project's default login when the run named none. Done
        # BEFORE the URL is settled, because a bypass profile supplies
        # its own logged-in landing URL and that is more specific than a
        # project-wide base_url.
        if needs_login and ctx.profile is not None:
            fallback_profile = ctx.profile
            allowed_domains = fallback_profile.allowed_domains or []
            if (fallback_profile.kind or "standard") == "bypass":
                try:
                    bypass_url, cookies = _resolve_bypass_profile(fallback_profile)
                    # The bypass profile's own target_url wins over a
                    # generic base_url and over a URL the agent would
                    # otherwise have taken from the goal text — it is the
                    # address the injected cookie is actually valid for.
                    environment_url = bypass_url
                    needs_url = False
                    logger.info(
                        "Run %s using project-default bypass profile '%s' "
                        "(%d cookie(s) will be injected)",
                        getattr(run, "id", None),
                        fallback_profile.name,
                        len(cookies or []),
                    )
                except Exception as exc:
                    # A misconfigured bypass profile must be loud, and it
                    # must fail the SAME way here as it does when the run
                    # names the profile explicitly (the branch at the top
                    # of this function lets _resolve_bypass_profile
                    # propagate). Logging and continuing — which this used
                    # to do — is what made a Skills run look like it never
                    # used the bypass at all: with the token fetch dead,
                    # the run started unauthenticated, the agent typed a
                    # work email into the real login form and ran itself
                    # out of steps, and the QA engineer was handed a
                    # verdict about job creation rather than "bypass login
                    # failed". Against a CAPTCHA-gated app an
                    # unauthenticated run is not a degraded run, it is a
                    # guaranteed false result.
                    logger.error(
                        "Run %s: project-default bypass profile '%s' failed to "
                        "resolve (%s). Failing the run rather than continuing "
                        "unauthenticated.",
                        getattr(run, "id", None),
                        fallback_profile.name,
                        exc,
                    )
                    raise RuntimeError(
                        f"Bypass login failed for credential profile "
                        f"'{fallback_profile.name}': {exc}. The run was not "
                        f"started — no test result was produced. Check the "
                        f"profile's API base URL, bypass endpoint and API key "
                        f"under Test setup."
                    ) from exc
            elif fallback_profile.credentials_json:
                try:
                    from app.services.credential_service import decrypt_credentials
                    sensitive_data = decrypt_credentials(
                        fallback_profile.credentials_json
                    )
                except Exception as exc:
                    # Same reasoning as the bypass branch above: a login
                    # that was configured but cannot be used is a setup
                    # failure, not a test result.
                    logger.error(
                        "Run %s: failed to decrypt project-default credentials for "
                        "profile '%s' (%s). Failing the run rather than continuing "
                        "unauthenticated.",
                        getattr(run, "id", None),
                        fallback_profile.name,
                        exc,
                    )
                    raise RuntimeError(
                        f"Could not read the stored credentials for profile "
                        f"'{fallback_profile.name}': {exc}. The run was not "
                        f"started — no test result was produced."
                    ) from exc

        if needs_url and ctx.has_url:
            environment_url = ctx.environment_url

    # Credentials will be typed into the page, but the profile carries no
    # allowlist — ai_runner hard-requires one whenever sensitive_data is
    # set and raises otherwise, which previously made such a profile
    # unusable rather than merely unguarded. Derive the target's own host:
    # strictly tighter than running unrestricted, and it keeps the safety
    # gate satisfied honestly instead of switching it off.
    if sensitive_data and not allowed_domains and not unrestricted_domains:
        derived = derive_allowed_domains(environment_url)
        if derived:
            logger.info(
                "Derived allowed_domains=%s from target URL for run %s "
                "(credential profile configured none)",
                derived,
                getattr(run, "id", None),
            )
            allowed_domains = derived

    # Let the agent follow a saved profile's own SSO/OAuth redirects
    # (Google, Microsoft, etc.) instead of being blocked by browser-use's
    # navigation allowlist the moment a "Log in with X" button opens the
    # provider's real login page. See _OAUTH_PROVIDER_DOMAINS above for the
    # rationale and its limits. No-op when allowed_domains is still None
    # (ad-hoc path, or no login at all).
    allowed_domains = _widen_allowed_domains_for_sso(allowed_domains)

    return environment_url, allowed_domains, sensitive_data, cookies, unrestricted_domains


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


def _final_screenshot_b64(result: dict) -> str | None:
    """Best-effort raw base64 (no `data:image/png;base64,` prefix -- that's
    added back by llm_router._build_messages) of the run's last available
    screenshot, for the expected-results visual judge pass.

    Every screenshot_url this codebase produces (ai_runner.py, android_runner.py)
    is already a data: URI, never a served file path -- confirmed by direct
    inspection, see app/services/ai_eval.py's evaluate_expected_results
    docstring. Returns None if nothing usable is found -- the caller already
    treats that as "nothing to check" without failing the run.

    Source order (BUG FIX 2026-07-28):

    1. On a FAILED run, the failing step's own screenshot. The agent never
       reached a meaningful end state, so the moment it gave up is what a
       human or a vision model actually wants to look at.
    2. result["final_screenshot_b64"] -- a dedicated end-of-run frame that
       ai_runner._execute_steps now captures unconditionally. This exists
       because every screenshot on that path used to be skipped whenever a
       video was recording, and Functional Tests are the only flow that
       records video: the result was that this function returned None on
       100% of Functional Test runs and evaluate_expected_results, which is
       built specifically for them, never ran once.
    3. Legacy fallback: scan events from the end for the most recent
       screenshot. Kept for result dicts that predate (2) or come from
       another runner -- android_runner has no final_screenshot_b64 key, so
       it lands here and behaves exactly as it did before.

    Note on ordering: (2) deliberately outranks a mid-run screenshot found
    by (3). An app-error evidence frame (see error_detection.py) sits on the
    step where the error appeared, not at the end, and grading a test's
    "Expected Results" against a mid-run image would misreport what the run
    finished with.
    """
    if result.get("status") == "failed":
        failing = result.get("failing_step") or {}
        shot = failing.get("screenshot_url")
        if shot and isinstance(shot, str) and "base64," in shot:
            return shot.split("base64,", 1)[1]

    final_b64 = result.get("final_screenshot_b64")
    if final_b64 and isinstance(final_b64, str):
        # Already raw base64 (no data: prefix) by construction in ai_runner.
        return final_b64

    failing = result.get("failing_step") or {}
    shot = failing.get("screenshot_url")
    if not shot:
        for event in reversed(result.get("events", [])):
            shot = event.get("screenshot_url")
            if shot:
                break
    if not shot or not isinstance(shot, str) or "base64," not in shot:
        return None
    return shot.split("base64,", 1)[1]


def _persist_result(db, run, run_id: str, result: dict) -> None:
    """Persist a finished run result (events, status, timing, summaries).

    Generates the LLM narrative summary here (single post-run call); if it
    fails the raw engine summary is kept — the run is never blocked on it.

    run.status is NOT always result["status"] verbatim any more (Phase 4,
    D.15): an agent-reported "passed" run can be downgraded to
    "needs_review" by any of three independent gates below — a GEval
    action-trace score under threshold, an application error observed on the
    page during the run (result["app_errors"], 2026-07-28), or too few of the
    test's own expected results confirmed on the final screen (2026-07-28).
    Each covers a failure mode the other two are blind to. Callers that care about the
    final, human-facing outcome (skill auto-save, replay stats, logging)
    must read run.status AFTER this function returns, not result["status"]."""
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
            run_id=run_id,
        )
    except Exception:
        logger.exception("Narrative summary generation raised; keeping raw summary")

    # Vibe Testing quality score (DeepEval GEval) -- New Vibe Test / Skill
    # Replay, on either platform (Phase 4, D.16, 2026-07-28: opened up to
    # Android -- android_runner.run_android_test_sync already returns the
    # same {status, summary, events, ...} shape as ai_runner's web path,
    # with events already using step_type="ai_scoped"/status/description
    # identically, so no Android-specific evaluator was needed; see
    # app/services/ai_eval.py's module docstring for the full finding).
    # Only runs once there's a real trajectory to judge (passed/failed;
    # never inconclusive/cancelled, which have no meaningful agent actions
    # to score). Autonomous QA (orchestrator.py) has its own persistence
    # path and never calls this function at all, so it's unaffected by
    # construction, not by a check here.
    #
    # Best-effort by contract (app.services.ai_eval.evaluate_run never
    # raises), but still wrapped here: a failure importing the module
    # itself (e.g. deepeval not installed) must never block run persistence.
    eval_result = None
    if result["status"] in ("passed", "failed"):
        try:
            from app.services.ai_eval import evaluate_run
            eval_result = evaluate_run(
                goal=run.goal,
                events=result.get("events", []),
                # The agent's own literal self-report, not the narrative
                # rewrite above -- judging the agent's actual claim against
                # its actual actions is the point; running it through a
                # second summarizing LLM call first would add another layer
                # of possible drift between what happened and what's judged.
                summary=raw_summary,
                run_id=run_id,
            )
        except Exception:
            logger.exception("Vibe Test quality scoring raised; leaving eval fields unset")
            eval_result = None
        if eval_result:
            run.eval_score = eval_result["score"]
            run.eval_reason = eval_result["reason"]
            run.eval_status = "completed"
            run.eval_metric = eval_result["metric"]
        else:
            run.eval_status = "unavailable"

    # Second, complementary judge pass (Phase 4 checklist bullet 7):
    # final-state screenshot vs. this run's own `expected_results` --
    # Functional Test only (nothing to check a legacy free-text goal run
    # against), independent of the action-trace eval_score above. See
    # app/services/ai_eval.py's evaluate_expected_results docstring for why
    # this is a separate signal rather than folded into eval_score.
    #
    # 2026-07-28: this now GATES as well (see the expected-results gate
    # below), where it previously only recorded a number. Turned on by
    # explicit product decision after a Happy Path run in which a "Call
    # screening" round was never created, the application displayed no error
    # of any kind, and the run reported passed -- a silent no-op. In-run
    # error detection is structurally blind to that case (there is nothing on
    # the page to detect) and the action-trace judge only sees the agent's
    # own actions, so comparing the final screen against the test's own
    # Expected Results is the only mechanism that can catch it.
    #
    # Hoisted out of the if-block below so the gate can distinguish "scored
    # and scored low" from "never scored" (not a Functional Test, no expected
    # results, or a non-terminal status) -- conflating the two would let a
    # skipped check silently gate a run.
    visual_eval_result = None
    if (
        (run.test_category or None) == "functional"
        and run.expected_results
        and result["status"] in ("passed", "failed")
    ):
        try:
            from app.services.ai_eval import evaluate_expected_results
            visual_eval_result = evaluate_expected_results(
                expected_results=run.expected_results,
                screenshot_b64=_final_screenshot_b64(result),
                goal=run.goal,
                run_id=run_id,
            )
        except Exception:
            logger.exception(
                "Expected-results visual pass raised; leaving visual_eval fields unset"
            )
            visual_eval_result = None
        if visual_eval_result:
            run.visual_eval_score = visual_eval_result["score"]
            run.visual_eval_reason = visual_eval_result["reason"]
            run.visual_eval_status = "completed"
            run.visual_eval_metric = visual_eval_result["metric"]
        else:
            run.visual_eval_status = "unavailable"

    # Phase 4 (D.15, D.21): GEval now gates the displayed status instead of
    # sitting next to it as purely informational fields. Per explicit
    # product decision (2026-07-28), this applies to every run scored above
    # -- not just rows from the structured Functional Test flow -- so a
    # legacy free-text goal run or a Skill Replay rerun is gated the same
    # way. Asymmetric on purpose: a low score can only downgrade an
    # agent-reported "passed" to "needs_review" for a human to decide; it
    # never upgrades an agent-reported "failed" into anything else, and a
    # run with no usable score (eval_result is None -- scoring unavailable,
    # not scoring-that-scored-low) is left exactly as the agent reported it,
    # so an infra hiccup in scoring can never itself cause a false
    # needs_review. Escalation is flag-only by design decision (2026-07-28,
    # D.21) -- no automatic re-run on a low score; the current AXON->Google->
    # OpenRouter chain has no distinct "more capable" tier to retry on, and
    # silently doubling a run's cost/latency was judged worse than a human
    # reviewing a flagged run.
    final_status = result["status"]
    if final_status == "passed" and eval_result is not None:
        from app.services.ai_eval import get_eval_threshold
        threshold = get_eval_threshold()
        if eval_result["score"] < threshold:
            final_status = "needs_review"
            logger.warning(
                "AI run %s: agent self-reported passed but GEval score "
                "%.3f is below threshold %.3f -- flagging needs_review",
                run_id, eval_result["score"], threshold,
            )

    # ── App-error gate (BUG FIX 2026-07-28) ─────────────────────────────
    # An agent can complete every action it planned while the APPLICATION
    # UNDER TEST was visibly failing — the exact bug this gate exists for:
    # a Happy Path run clicked 'Generate with AI', the app rendered "Error:
    # Failed to start question generation", and the run still reported
    # passed. app.services.error_detection.RunErrorWatcher now catches those
    # during the run (see ai_runner.resolve_with_ai's post_action_check) and
    # ai_runner returns them here as result["app_errors"].
    #
    # Same asymmetry as the GEval gate above and for the same reason: this
    # can only downgrade an agent-reported "passed"; it never touches an
    # already-"failed"/inconclusive/cancelled run. Independent of eval_result
    # on purpose — this is direct observed evidence from the live page, not a
    # model's opinion, so it must still gate when scoring was unavailable.
    #
    # Deliberately "needs_review" rather than "failed", even though the app
    # genuinely errored: the agent may have recovered and completed the goal
    # anyway, and a transient/unrelated error should not silently become a
    # hard product failure. A human sees the evidence and decides.
    # Absent from every non-web/legacy result dict (android_runner,
    # orchestrator), so .get() makes this a no-op there by construction.
    # Guarded on result["status"], NOT final_status: all three gates downgrade
    # to the same "needs_review", so short-circuiting on final_status would
    # change nothing about the outcome while silently dropping this gate's
    # reason whenever the GEval gate happened to fire first. A reviewer opening
    # a flagged run would then see only "the quality score was low" and never
    # learn that the application had thrown a concrete error -- by far the more
    # actionable of the two. Every gate evaluates independently; the status is
    # idempotent, the reasons accumulate.
    app_errors = result.get("app_errors") or []
    if result["status"] == "passed" and app_errors:
        final_status = "needs_review"
        logger.warning(
            "AI run %s: agent self-reported passed but %d application error "
            "state(s) were detected on the page during the run -- flagging "
            "needs_review. First: %r (source=%s, step=%s)",
            run_id,
            len(app_errors),
            app_errors[0].get("message"),
            app_errors[0].get("source"),
            app_errors[0].get("sequence"),
        )
        # Make the cause visible in the UI. RunDetail renders eval_reason as
        # the explanation under the needs_review banner; prepend the concrete
        # observed evidence and keep any GEval reasoning after it rather than
        # overwriting a second, independent signal.
        note = (
            f"Application error detected during the run: "
            f"\"{app_errors[0].get('message')}\""
            + (f" (+{len(app_errors) - 1} more)" if len(app_errors) > 1 else "")
        )
        run.eval_reason = f"{note} | {run.eval_reason}" if run.eval_reason else note

    # ── Expected-results gate (2026-07-28) ──────────────────────────────
    # Catches the SILENT failure: the agent completed every action, the
    # application displayed no error at all, and yet the thing the test was
    # supposed to produce simply is not there. Nothing else in this pipeline
    # can see that -- in-run detection needs something rendered on the page,
    # and the action-trace judge only ever sees the agent's own actions.
    #
    # Same asymmetry and same never-gate-on-a-missing-score rule as the two
    # gates above: only an agent-reported "passed" can be downgraded, and
    # only when the judge actually produced a score. visual_eval_result is
    # None whenever the check was skipped (not a Functional Test, no expected
    # results, no screenshot, vision call failed), and None must never gate
    # -- otherwise every non-Functional run would land in review.
    #
    # Threshold is deliberately not 1.0; see ai_eval.get_visual_eval_threshold
    # for why (the judge is instructed to answer "unconfirmed" when it cannot
    # tell, so some expected results are unconfirmable by construction rather
    # than by defect). Tune with VIBE_TEST_VISUAL_EVAL_THRESHOLD.
    # Guarded on result["status"] for the same reason as the gate above --
    # every cause must be recorded, not just whichever one happened to fire
    # first.
    if result["status"] == "passed" and visual_eval_result is not None:
        from app.services.ai_eval import get_visual_eval_threshold

        visual_threshold = get_visual_eval_threshold()
        if visual_eval_result["score"] < visual_threshold:
            final_status = "needs_review"
            unconfirmed = visual_eval_result.get("unconfirmed") or []
            logger.warning(
                "AI run %s: agent self-reported passed but only %.0f%% of the "
                "test's expected results could be confirmed on the final "
                "screen (threshold %.0f%%) -- flagging needs_review. "
                "Unconfirmed: %s",
                run_id,
                visual_eval_result["score"] * 100,
                visual_threshold * 100,
                "; ".join(unconfirmed[:5]) or "(none listed)",
            )
            # Name the specific expected results that were not confirmed --
            # that is the actionable part for whoever reviews this, far more
            # than the score on its own. Capped so a test with 50 expected
            # results cannot produce an unreadable wall of text.
            note = "Expected results not confirmed on the final screen: " + (
                "; ".join(f'"{u}"' for u in unconfirmed[:3]) or "(see visual check)"
            )
            if len(unconfirmed) > 3:
                note += f" (+{len(unconfirmed) - 3} more)"
            run.eval_reason = f"{note} | {run.eval_reason}" if run.eval_reason else note

    failing = result.get("failing_step")
    if not failing and app_errors:
        # No mechanical failing step, but the app errored — surface the first
        # app error as the run's failing step so the Run Detail view has
        # something concrete (description + screenshot evidence) to show
        # instead of an unexplained needs_review.
        first = app_errors[0]
        failing = {
            "sequence": first.get("sequence"),
            "description": f"Application error: {first.get('message')}",
            "screenshot_url": first.get("screenshot_url"),
        }

    run.status = AIRunStatus(final_status)
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
    swallowed: skill capture must never fail run persistence.

    Phase 7 (F.26): when no exact goal_hash match exists, falls back to a
    conservative fuzzy match (app.services.skill_store.find_similar_skill)
    against this run's own project before creating a brand-new row — a
    minor rewording of the same test (typo fix, reordered clause) now
    refreshes the existing skill's history instead of fragmenting it into a
    second, disconnected row with its own separate replay/flakiness count
    starting back at zero. See skill_store.py's module docstring for why
    the match threshold is deliberately biased against false merges."""
    from app.models.ai_runs import AISkill

    try:
        if not history_json:
            return
        if (run.run_type or "ai") != "ai":
            return

        goal_hash = _goal_hash(run.goal)
        step_count = run.step_count or 0
        skill = db.query(AISkill).filter(AISkill.goal_hash == goal_hash).one_or_none()

        merged_via_fuzzy_match = False
        if skill is None:
            from app.services.skill_store import find_similar_skill

            candidates = (
                db.query(AISkill)
                .filter(AISkill.project_id == run.project_id, AISkill.goal_hash != goal_hash)
                .all()
            )
            matched, ratio = find_similar_skill(candidates, run.goal)
            if matched is not None:
                skill = matched
                merged_via_fuzzy_match = True
                logger.info(
                    "Skill %s: fuzzy-matched run %s to an existing skill "
                    "(similarity=%.3f) instead of creating a duplicate — "
                    "old goal=%r new goal=%r",
                    skill.id, run.id, ratio, skill.goal, run.goal,
                )

        if skill is not None:
            skill.history_json = history_json
            skill.step_count = step_count
            skill.source_run_id = run.id
            skill.environment = run.environment
            skill.credential_profile_id = run.credential_profile_id
            skill.project_id = run.project_id
            if merged_via_fuzzy_match:
                # Converge identity to this run's exact wording — a later
                # exact resubmission of *this* phrasing now hits the fast
                # goal_hash lookup directly instead of needing another
                # fuzzy pass every time.
                skill.goal = run.goal
                skill.goal_hash = goal_hash
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
    soft_time_limit=_VIBE_TEST_SOFT_TIME_LIMIT_S,
    time_limit=_VIBE_TEST_HARD_TIME_LIMIT_S,
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
            environment_url, allowed_domains, sensitive_data, cookies, unrestricted_domains = (
                _resolve_run_inputs(db, run)
            )
            llm_override = _resolve_hands_llm_override(run.goal, environment_url, sensitive_data)

            event_sink = _make_live_event_sink(run_id)

            # Functional Test only (New Vibe Test Phase 2) — read before
            # closing the DB session below. Falls back to "desktop" (today's
            # fixed behavior) for every non-Functional-Test run and any
            # unrecognized value, rather than letting a bad preset 500 the
            # whole run.
            from app.services.ai_runner import VIEWPORT_PRESETS

            viewport = VIEWPORT_PRESETS.get(
                run.viewport_preset or "desktop", VIEWPORT_PRESETS["desktop"]
            )

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
                allow_unrestricted_domains=unrestricted_domains,
                run_id=run_id,
                enable_live_capture=True,
                on_video_ready=lambda path: _persist_video_path(run_id, path),
                max_steps=_VIBE_TEST_MAX_STEPS,
                max_duration_s=None,
                viewport=viewport,
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
        logger.info("AI run %s completed with status: %s", run_id, run.status.value)

        # Auto-save a replayable skill from passed AI-planned runs. Reads
        # run.status (post-GEval-gating, Phase 4), not result["status"] --
        # a run downgraded to needs_review must not be captured as a
        # trusted, reusable skill just because the agent itself claimed
        # success; only a run that's actually still showing "passed" once
        # the independent check has weighed in should be.
        if run.status == AIRunStatus.passed:
            _maybe_save_skill(db, run, result.get("history_json"))

        # If this run started from clicking Replay/Run on a skill (prompt
        # skill with no recording yet), keep its replay bookkeeping current.
        if run.skill_id:
            _maybe_bump_skill_stats(db, run, run.status.value)
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

        environment_url, allowed_domains, sensitive_data, cookies, unrestricted_domains = (
            _resolve_run_inputs(db, run)
        )
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
            allow_unrestricted_domains=unrestricted_domains,
            run_id=run_id,
            enable_live_capture=True,
            on_video_ready=lambda path: _persist_video_path(run_id, path),
        )

        db = SessionLocal()
        run = db.get(AITestRun, run_id)
        if run is None:
            return
        if run.status == AIRunStatus.cancelled:
            logger.info("Skill replay run %s was cancelled during execution", run_id)
            return

        _persist_result(db, run, run_id, result)
        _maybe_bump_skill_stats(db, run, run.status.value)
        db.commit()
        logger.info("Skill replay %s completed with status: %s", run_id, run.status.value)

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
