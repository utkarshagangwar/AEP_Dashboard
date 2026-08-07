"""Resolve where an AI/skill run should start, and who it logs in as.

Why this module exists
----------------------
Before this, "what URL does this run open?" was answered in exactly one
place — app.workers.tasks.ai_execution._resolve_run_inputs — and only for
two of the three ways a run can be created:

  * a kind="bypass" credential profile  -> profile.target_url
  * an ad-hoc "Website with/without login" run -> run.adhoc_target_url
  * everything else -> "about:blank"

That third branch is the bug. A kind="standard" profile supplied
credentials and no address; a prompt skill extracted from a SOW (see
app.services.skill_store.upsert_prompt_skill) has neither, because parse
time knows nothing about environments or credentials. Both resolved to
"about:blank", ai_runner skipped page.goto() on its
`!= "about:blank"` guard, and the agent opened a blank tab — the failure
surfaced to the QA engineer as "the browser encountered a blank page"
with no indication that no navigation had ever been attempted.

Fixing that inside the Celery task alone would leave the same defect
half-open: the API would still accept and queue a run that cannot
possibly navigate, burning a browser session and LLM tokens to produce a
vague failure minutes later. So resolution lives here, and BOTH callers
use it:

  * app/api/v1/ai_runs.py  — at submit time, to reject an unrunnable run
    with a specific, actionable reason (fail fast).
  * app/workers/tasks/ai_execution.py — at run time, to produce the
    actual environment_url / allowed_domains handed to ai_runner.

Deliberately secret-free
------------------------
This module reads `AICredentialProfile` rows but NEVER decrypts
`credentials_json`. Decryption stays in the worker
(app.services.credential_service, called from ai_execution), so the API
process — which serves this data to a browser — has no path that puts a
plaintext credential in scope. The resolver returns the profile's
identity and address only.

Precedence
----------
Highest wins. URL and LOGIN are resolved INDEPENDENTLY — that
independence is load-bearing, not incidental:

  URL:    explicit profile.target_url
          -> run.adhoc_target_url
          -> project environment base_url
  Login:  explicitly requested profile
          -> Project.default_credential_profile_id

Why login no longer depends on the environment lookup
-----------------------------------------------------
It used to. The project default lived on the ProjectEnvironment row, so
resolving a login required an environment-label match first. A skill
extracted from a SOW has environment = NULL, so any miss — no rows saved
yet, an unmatched label, or two rows and no label to choose between them
— silently dropped the login along with the URL.

The run then started unauthenticated. Against a CAPTCHA-gated app that
is not a degraded run, it is a guaranteed failure: the agent navigates
to the app, gets bounced to the real login form, and hits the reCAPTCHA
that a kind="bypass" profile exists precisely to route around. Confirmed
against dev.interviewgod.ai — the action log contained no "Inject
authenticated session cookie" step at all.

The login now comes off the project directly (migration 0042) and is
resolved even when no environment row matches, so a missing URL and a
missing login are independent failures with independent messages.

Why a goal-embedded URL no longer suppresses the login
-----------------------------------------------------
`goal_contains_url` exempts a run from the caller's fail-fast gate,
because a goal like "go to https://x.test and log in" navigates itself.
That exemption previously short-circuited the whole resolution, so a
SOW goal that happened to mention its own URL skipped login attachment
entirely and ran unauthenticated with no warning. The exemption now
waives ONLY the URL requirement; the login is always resolved and
attached.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from app.core.logging import get_logger

logger = get_logger(__name__)

# ai_runner treats this sentinel as "no navigation" — see the
# `environment_url != "about:blank"` guards in _execute_steps and
# _replay_history. Named here so the contract is stated once rather than
# duplicated as a bare string across three modules.
NO_NAVIGATION_URL = "about:blank"

# A goal may legitimately carry its own address ("go to https://x.test and
# ..."), which is how free-text Vibe Test goals have always worked. Such a
# run is NOT unrunnable just because no profile or project environment is
# configured, so the fail-fast gate must exempt it. Matches an http(s) URL
# anywhere in the text.
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def goal_contains_url(goal: Optional[str]) -> bool:
    """True if the goal text itself names an http(s) address for the agent
    to navigate to. Used only to exempt such goals from the fail-fast
    gate — the agent, not this resolver, does the actual navigating."""
    return bool(goal and _URL_IN_TEXT.search(goal))


def derive_allowed_domains(url: Optional[str]) -> Optional[list[str]]:
    """Best-effort single-host allowlist from a URL, or None if the URL is
    absent/unparseable.

    Used only to supply a guardrail where there was none: when credentials
    will be typed into a page (browser-use `sensitive_data`) and the
    resolved credential profile has no `allowed_domains` configured.
    ai_runner hard-requires an allowlist in that situation and otherwise
    raises. Returning the target's own host keeps that safety gate intact
    and is strictly tighter than the alternative of running unrestricted.
    """
    if not url or url == NO_NAVIGATION_URL:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        # urlsplit raises on malformed IPv6 literals etc. Never let a bad
        # stored URL take down resolution — the caller degrades to "no
        # allowlist derived", which the sensitive_data gate then rejects
        # loudly rather than running unguarded.
        logger.warning("Could not parse host from URL %r for allowlist derivation", url)
        return None
    return [host] if host else None


@dataclass
class StartContext:
    """The resolved answer to "where does this run start, and as whom?".

    environment_url is NO_NAVIGATION_URL when nothing resolved — callers
    must treat that as "the agent will open a blank tab", which is a
    failure for every run whose goal does not carry its own URL.
    """

    environment_url: str
    #: The AICredentialProfile row to authenticate with, or None. Not
    #: decrypted — see the module docstring.
    profile: Optional[object] = None
    #: Where environment_url came from, for logging and for the run
    #: summary shown to the QA engineer. One of: "credential_profile",
    #: "adhoc", "project_environment", "none".
    url_source: str = "none"
    #: Where `profile` came from: "explicit", "project_environment", "none".
    profile_source: str = "none"
    allowed_domains: Optional[list[str]] = None
    #: Human-readable explanation of what is missing, when nothing
    #: resolved. Surfaced verbatim in the API's 400 so the engineer is
    #: told which project/environment to configure, not just "failed".
    reason: Optional[str] = None

    @property
    def has_url(self) -> bool:
        return bool(
            self.environment_url and self.environment_url != NO_NAVIGATION_URL
        )


def _lookup_project_environment(db, project_id, environment):
    """Find the ProjectEnvironment row for (project_id, environment).

    Exact label match first. If the run carries no environment label at
    all — which is the common case for a SOW-extracted skill, whose
    `environment` is whatever the ingest happened to set, often None —
    fall back to the project's single configured environment, but ONLY
    when it has exactly one. Guessing between a project's dev and
    production addresses is not a call this code gets to make silently:
    with two or more configured and no label to choose by, it resolves
    nothing and the caller reports exactly that.
    """
    from app.models.project import ProjectEnvironment

    if not project_id:
        return None

    try:
        rows = (
            db.query(ProjectEnvironment)
            .filter(ProjectEnvironment.project_id == project_id)
            .all()
        )
    except Exception as exc:
        # A resolution failure must never be indistinguishable from "not
        # configured" — log loudly, then degrade to unresolved so the
        # fail-fast gate reports it rather than launching a blank browser.
        logger.exception(
            "Failed to load project environments for project %s: %s", project_id, exc
        )
        return None

    if not rows:
        return None

    if environment:
        wanted = environment.strip().lower()
        for row in rows:
            if (row.environment or "").strip().lower() == wanted:
                return row
        # An explicit label that matches nothing is a configuration gap,
        # not an invitation to substitute a different environment. Never
        # silently run a "staging" skill against production.
        return None

    if len(rows) == 1:
        return rows[0]

    return None


def _describe_missing(db, project_id, environment) -> str:
    """Build the operator-facing explanation for an unresolvable run.

    Names the specific project and environment so the engineer knows
    exactly which screen to go configure, instead of being told a run
    'failed to start'.
    """
    from app.models.project import Project, ProjectEnvironment

    if not project_id:
        return (
            "This run has no start URL: it is not assigned to a project, has no "
            "credential profile, and its instructions do not contain a URL. "
            "Assign it to a project with a configured environment, or pick a "
            "credential profile with a target URL."
        )

    project = None
    try:
        project = db.get(Project, project_id)
    except Exception:  # pragma: no cover - diagnostics only, never fatal
        logger.exception("Could not load project %s while describing failure", project_id)
    project_name = getattr(project, "name", None) or str(project_id)

    configured: list[str] = []
    try:
        configured = [
            row.environment
            for row in db.query(ProjectEnvironment)
            .filter(ProjectEnvironment.project_id == project_id)
            .all()
            if row.base_url
        ]
    except Exception:  # pragma: no cover - diagnostics only, never fatal
        logger.exception("Could not list environments for project %s", project_id)

    if not configured:
        return (
            f"Project '{project_name}' has no environment URL configured, so there "
            f"is nowhere for this run to start. Add a base URL for it under "
            f"Project settings -> Environments, or pick a credential profile that "
            f"has a target URL."
        )

    if environment:
        return (
            f"Project '{project_name}' has no URL configured for environment "
            f"'{environment}'. Configured environments: {', '.join(sorted(configured))}. "
            f"Add a base URL for '{environment}', or run this against one of the "
            f"environments above."
        )

    return (
        f"This run does not say which environment to use, and project "
        f"'{project_name}' has more than one configured "
        f"({', '.join(sorted(configured))}). Choose an environment, or pick a "
        f"credential profile with a target URL."
    )


def resolve_start_context(
    db,
    *,
    project_id=None,
    environment: Optional[str] = None,
    credential_profile_id=None,
    adhoc_target_url: Optional[str] = None,
    goal: Optional[str] = None,
) -> StartContext:
    """Resolve the start URL and credential profile for a run.

    Never raises for ordinary "not configured" situations — those come
    back as a StartContext with has_url False and `reason` set, so the
    caller decides whether that is a 400 (API submit) or a run-time
    failure (worker). Only genuinely unexpected conditions log.

    `goal` is used solely to decide whether an unresolved URL is
    acceptable (a goal carrying its own http(s) address navigates
    itself). It is never parsed for a URL to navigate to here — that
    remains the agent's job, unchanged.
    """
    from app.models.ai_runs import AICredentialProfile
    from app.models.project import Project

    profile = None
    profile_source = "none"
    url: Optional[str] = None
    url_source = "none"
    allowed_domains: Optional[list[str]] = None

    # ── 1. Explicitly requested credential profile ──────────────────────
    if credential_profile_id:
        try:
            profile = db.get(AICredentialProfile, credential_profile_id)
        except Exception as exc:
            logger.exception(
                "Failed to load credential profile %s: %s", credential_profile_id, exc
            )
            profile = None
        if profile is not None:
            profile_source = "explicit"
            allowed_domains = profile.allowed_domains or None
            # target_url now counts for EVERY kind, not just "bypass".
            # A standard profile previously supplied a password with
            # nowhere to type it; this is the line that fixes that.
            if profile.target_url:
                url = profile.target_url
                url_source = "credential_profile"

    # ── 2. Ad-hoc one-off target (never a saved profile) ────────────────
    # Only consulted when no profile URL won above, preserving the
    # existing mutual exclusivity enforced by AIRunCreate's validator.
    if url is None and adhoc_target_url:
        url = adhoc_target_url
        url_source = "adhoc"

    # ── 3. Project environment base URL ─────────────────────────────────
    # Consulted only when 1 and 2 produced no URL, so it cannot alter a
    # run that already resolved one.
    if url is None:
        env_row = _lookup_project_environment(db, project_id, environment)
        if env_row is not None and env_row.base_url:
            url = env_row.base_url
            url_source = "project_environment"

    # ── 4. Project default login ────────────────────────────────────────
    # Resolved INDEPENDENTLY of step 3. A run that could not resolve a
    # URL must still get its login, and vice versa — coupling the two is
    # what let a SOW skill (environment = NULL) run unauthenticated
    # straight into a CAPTCHA. See the module docstring.
    if profile is None and project_id:
        project = None
        try:
            project = db.get(Project, project_id)
        except Exception as exc:
            logger.exception("Failed to load project %s: %s", project_id, exc)
        default_profile_id = getattr(project, "default_credential_profile_id", None)
        if default_profile_id:
            try:
                profile = db.get(AICredentialProfile, default_profile_id)
            except Exception as exc:
                logger.exception(
                    "Failed to load project-default credential profile %s: %s",
                    default_profile_id,
                    exc,
                )
                profile = None
            if profile is not None:
                profile_source = "project_default"
                if allowed_domains is None:
                    allowed_domains = profile.allowed_domains or None

    # A bypass profile carries its own logged-in landing URL, which is
    # more specific than a project-wide base_url — prefer it, matching
    # how an explicitly chosen bypass profile already behaves. Only
    # overrides a generic project base_url, never an explicit or ad-hoc
    # choice the run made for itself.
    if (
        profile is not None
        and getattr(profile, "target_url", None)
        and url_source in ("project_environment", "none")
    ):
        url = profile.target_url
        url_source = "credential_profile"

    if url is None:
        return StartContext(
            environment_url=NO_NAVIGATION_URL,
            profile=profile,
            url_source="none",
            profile_source=profile_source,
            allowed_domains=allowed_domains,
            reason=(
                None
                if goal_contains_url(goal)
                else _describe_missing(db, project_id, environment)
            ),
        )

    logger.info(
        "Resolved start context: url=%s (source=%s) profile=%s (source=%s)",
        url,
        url_source,
        getattr(profile, "name", None),
        profile_source,
    )

    return StartContext(
        environment_url=url,
        profile=profile,
        url_source=url_source,
        profile_source=profile_source,
        allowed_domains=allowed_domains,
    )
