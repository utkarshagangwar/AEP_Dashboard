"""Project Intelligence — Phase 3: Active Scheduled Crawler & Visual.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §14.3, §24, table 7,
table 8, table 13. This module is the actual crawl + vision-extraction
logic; workers/tasks/pi_crawl.py is the thin Celery wrapper around it
(scheduling, per-task gate re-validation, retry-free error containment).

NOT a new agent (spec §14.3): this reuses app.services.ai_runner.
run_ai_test_sync exactly as workers/tasks/ai_execution.py does for a New
Vibe Test run, with a crawl-oriented goal preset instead of a human-typed
one. Credential/URL resolution reuses app.services.start_context.
resolve_start_context (unmodified — see that module's own "files
explicitly not modified" list, spec §25.1) and, for a kind="bypass"
profile, app.workers.tasks.ai_execution._resolve_bypass_profile (imported,
never duplicated).

allowed_domains is pinned to the target environment's own host and ONLY
that host (spec §14.3: "allowed_domains set from the project environment's
host ... non-negotiable for an unattended agent", referencing
GHSA-x39x-9qw5-ghrf) — deliberately narrower than what a saved credential
profile's own `allowed_domains` might list, and without the OAuth-provider
widen app.workers.tasks.ai_execution._widen_allowed_domains_for_sso applies
to ordinary Vibe/Skill runs. That widen exists so a human-initiated run can
follow a real SSO popup; an unattended nightly crawl gets the stricter
reading instead — flagged here and in the Phase 3 report as an
interpretation, not a literal spec instruction, because the spec does not
address SSO at all for this path.

Fails open throughout, same contract as every other Project Intelligence
worker path (pi_ingest.py, pi_extract.py): a crawl that cannot resolve a
start URL, cannot authenticate, or hits any internal error degrades to a
skipped/logged no-op. Nothing here can fail a test run, because nothing
here runs anywhere near one — this is Celery Beat-triggered, independent
of every user-facing execution path.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Bounded, order-preserving sample of a run's screens — enough to steer a
# breadth-first crawl toward unvisited territory (spec §14.3: "Breadth-first
# with a visited set seeded from known screens") without inflating the goal
# prompt without bound on a project with hundreds of catalogued screens.
_MAX_KNOWN_ROUTES_IN_GOAL = 30

_SCREENSHOT_DATA_URL_PREFIX = "data:image/png;base64,"


# ── Screenshot storage (spec §16: "referenced, never duplicated") ───────────

def _screenshot_root() -> str:
    # Same VISUAL_DATA_DIR env var / default already used by
    # app/services/ai_run_capture.py and app/workers/tasks/visual_audit.py —
    # same shared volume, new subfolder, so this needs no new deploy config.
    base = os.environ.get("VISUAL_DATA_DIR", os.path.join(os.getcwd(), "visual_qa_data"))
    return os.path.join(base, "pi_crawl_screenshots")


def _screenshot_dir(project_id, environment_id) -> str:
    path = os.path.join(_screenshot_root(), str(project_id), str(environment_id or "none"))
    os.makedirs(path, exist_ok=True)
    return path


def _save_screenshot(project_id, environment_id, crawl_run_id, index: int, b64: str) -> Optional[str]:
    """Decode one base64 PNG and write it under VISUAL_DATA_DIR. Returns the
    path, or None on any failure (never raises — a screenshot save failure
    must not abort the crawl's own screen/component extraction)."""
    import base64

    try:
        directory = _screenshot_dir(project_id, environment_id)
        path = os.path.join(directory, f"{crawl_run_id}_{index:03d}.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(b64))
        return path
    except Exception:
        logger.warning(
            "pi_crawl: failed to save screenshot %d for crawl run %s",
            index, crawl_run_id, exc_info=True,
        )
        return None


def _sample_screenshots(events: list[dict], limit: int) -> list[str]:
    """Evenly-spaced base64 PNGs (prefix stripped) from a run's events, up
    to `limit`. Evenly spaced rather than "first N": the first few steps of
    a crawl are disproportionately login/landing screens, and sampling
    across the whole run gives the vision pass a better spread of the
    product's actual screens for the same fixed cost."""
    candidates: list[str] = []
    for ev in events or []:
        url = ev.get("screenshot_url") if isinstance(ev, dict) else None
        if isinstance(url, str) and url.startswith(_SCREENSHOT_DATA_URL_PREFIX):
            candidates.append(url[len(_SCREENSHOT_DATA_URL_PREFIX):])

    if len(candidates) <= limit:
        return candidates
    if limit <= 0:
        return []
    step = len(candidates) / float(limit)
    return [candidates[int(i * step)] for i in range(limit)]


# ── Goal construction (spec §14.3: breadth-first, visited set seeded from
#    known screens) ──────────────────────────────────────────────────────

def _known_routes(db, *, project_id, limit: int = _MAX_KNOWN_ROUTES_IN_GOAL) -> list[str]:
    from app.models.project_intelligence import PiScreen, PiStatus

    try:
        rows = (
            db.query(PiScreen.route)
            .filter(
                PiScreen.project_id == project_id,
                PiScreen.status.in_((PiStatus.pending, PiStatus.verified)),
            )
            .order_by(PiScreen.last_seen_at.desc())
            .limit(limit)
            .all()
        )
        return [r[0] for r in rows if r[0]]
    except Exception:
        logger.warning("pi_crawl: could not load known routes for project %s", project_id, exc_info=True)
        return []


def _build_crawl_goal(environment_url: str, known_routes: list[str]) -> str:
    lines = [
        f"You are performing an exploratory crawl of the web application at {environment_url}.",
        "Explore breadth-first: from each page, note every navigation option "
        "(menu items, tabs, links, buttons that open a new screen) before "
        "following any single one deeper.",
        "Prioritise pages you have not seen before over ones already known.",
        "Do NOT fill in or submit any form that creates, updates, or deletes "
        "data — no checkout, registration, delete, save, or edit-and-submit "
        "actions. Only navigate and observe. Read-only exploration.",
        "Do not log out.",
        "If you reach a login wall or an error you cannot get past, stop and "
        "summarise what you found rather than retrying indefinitely.",
    ]
    if known_routes:
        lines.append(
            "Pages already catalogued on this site (avoid revisiting these "
            "unless needed to reach a new page from them):"
        )
        lines.extend(f"  - {route}" for route in known_routes)
    return "\n".join(lines)


# ── Context resolution (credentials + allowed_domains) ──────────────────────

class _CrawlContext:
    __slots__ = ("environment_url", "allowed_domains", "sensitive_data", "cookies")

    def __init__(self, environment_url, allowed_domains, sensitive_data, cookies):
        self.environment_url = environment_url
        self.allowed_domains = allowed_domains
        self.sensitive_data = sensitive_data
        self.cookies = cookies


def _resolve_crawl_context(db, *, project, environment_row) -> Optional[_CrawlContext]:
    """Resolve (environment_url, allowed_domains, sensitive_data, cookies)
    for one crawl. Returns None when nothing safe/runnable resolves — the
    caller treats that as a skip, never an error.

    Deliberately reuses, not reimplements: app.services.start_context.
    resolve_start_context (unmodified) for the URL/login precedence, and
    app.workers.tasks.ai_execution._resolve_bypass_profile for a
    kind="bypass" profile's live token exchange — the same function New
    Vibe Test / Skill Replay runs already call, so a bypass profile behaves
    identically whether a human or the crawler is the caller.
    """
    from app.services.credential_service import decrypt_credentials
    from app.services.start_context import (
        NO_NAVIGATION_URL,
        derive_allowed_domains,
        resolve_start_context,
    )

    ctx = resolve_start_context(db, project_id=project.id, environment=environment_row.environment)

    environment_url = ctx.environment_url
    sensitive_data = None
    cookies = None

    if ctx.profile is not None:
        if (getattr(ctx.profile, "kind", None) or "standard") == "bypass":
            try:
                from app.workers.tasks.ai_execution import _resolve_bypass_profile

                environment_url, cookies = _resolve_bypass_profile(ctx.profile)
            except Exception:
                logger.warning(
                    "pi_crawl: bypass login failed for project %s environment %s "
                    "— skipping this crawl rather than running unauthenticated",
                    project.id, environment_row.environment, exc_info=True,
                )
                return None
        elif getattr(ctx.profile, "credentials_json", None):
            try:
                sensitive_data = decrypt_credentials(ctx.profile.credentials_json)
            except Exception:
                logger.warning(
                    "pi_crawl: could not decrypt credentials for project %s "
                    "environment %s — crawling unauthenticated",
                    project.id, environment_row.environment, exc_info=True,
                )

    if not environment_url or environment_url == NO_NAVIGATION_URL:
        logger.info(
            "pi_crawl: no resolvable start URL for project %s environment %s — skipped",
            project.id, environment_row.environment,
        )
        return None

    # Non-negotiable per spec §14.3/§24 — pinned to the target's own host,
    # not to whatever a saved profile's own allowed_domains lists (see
    # module docstring).
    allowed_domains = derive_allowed_domains(environment_url)
    if not allowed_domains:
        logger.warning(
            "pi_crawl: could not derive an allowed_domains guardrail from %r "
            "for project %s — refusing to crawl unrestricted",
            environment_url, project.id,
        )
        return None

    return _CrawlContext(environment_url, allowed_domains, sensitive_data, cookies)


# ── Vision-tier design pattern extraction (spec table 7, table 8) ───────────

_DESIGN_PATTERN_SYSTEM = (
    "You are reading a screenshot of a LIVE web application to record its "
    "OBSERVED visual design conventions for a QA/design reference tool.\n"
    "\n"
    "Record only what you can literally see in THIS screenshot — recurring "
    "colours, typography, spacing/layout conventions, and component styles "
    "(buttons, cards, forms, nav bars). Do not invent a pattern that is not "
    "visible, and do not describe the product's functionality — only its "
    "visual design.\n"
    "\n"
    "Respond with JSON only:\n"
    '{"patterns": [{"pattern_type": "color"|"typography"|"layout"|'
    '"component_style", "value": {<free-form observed detail>}, '
    '"description": "<one short sentence>"}]}\n'
    "\n"
    "Rules:\n"
    "  1. Approximate honestly. If you cannot read an exact hex value, "
    "describe the colour in words instead of guessing a precise code.\n"
    "  2. Merge duplicates — the same button style seen three times is one "
    "pattern, not three.\n"
    "  3. Up to 8 patterns per screenshot. Prefer the most visually "
    "distinctive/recurring ones.\n"
    "  4. If nothing distinctive is observable, return {\"patterns\": []} — "
    "a valid, correct answer."
)
_MAX_PATTERNS_PER_SHOT = 8
_MAX_VALUE_STR_CHARS = 300


def _normalize_patterns(raw: object) -> list[dict]:
    patterns = raw.get("patterns") if isinstance(raw, dict) else None
    if not isinstance(patterns, list):
        return []

    allowed_types = {"color", "typography", "layout", "component_style"}
    out: list[dict] = []
    for entry in patterns:
        if not isinstance(entry, dict):
            continue
        pattern_type = str(entry.get("pattern_type") or "").strip().lower()
        if pattern_type not in allowed_types:
            continue
        value = entry.get("value")
        if not isinstance(value, dict):
            # Tolerate a bare string value from the model rather than
            # dropping the whole pattern — still valid JSONB either way.
            value = {"detail": str(value)[:_MAX_VALUE_STR_CHARS]} if value else {}
        description = " ".join(str(entry.get("description") or "").split())[:500] or None
        out.append({"pattern_type": pattern_type, "value": value, "description": description})
        if len(out) >= _MAX_PATTERNS_PER_SHOT:
            break
    return out


def _analyze_screenshot(db, *, project_id, screenshot_path: str, screenshot_b64: str) -> int:
    """One bounded vision-tier LLM call for one screenshot. Writes
    pi_design_patterns rows (status=pending — the same review gate every
    other Project Intelligence table uses). Returns the count written.
    Never raises: a failed vision call loses this one screenshot's patterns,
    not the crawl's screen/component extraction, which has already
    committed by the time this runs (see run_crawl below)."""
    from app.models.project_intelligence import PiDesignPattern, PiStatus

    try:
        from app.services import llm_router

        result = llm_router.complete_json_complete(
            "Record the observed visual design patterns in this screenshot.",
            system=_DESIGN_PATTERN_SYSTEM,
            images_b64=[screenshot_b64],
            max_tokens=1024,
        )
    except Exception:
        logger.warning(
            "pi_crawl: vision call failed for screenshot %s", screenshot_path, exc_info=True,
        )
        return 0

    patterns = _normalize_patterns(result.parsed_json or {})
    written = 0
    for p in patterns:
        try:
            row = PiDesignPattern(
                project_id=project_id,
                pattern_type=p["pattern_type"][:50],
                value=p["value"],
                description=p["description"],
                evidence_ref=screenshot_path,
                status=PiStatus.pending,
            )
            db.add(row)
            written += 1
        except Exception:
            logger.warning(
                "pi_crawl: could not stage design pattern row for %s",
                screenshot_path, exc_info=True,
            )
    if written:
        db.flush()
    return written


# ── Entry point (called by workers/tasks/pi_crawl.py) ───────────────────────

def run_crawl(db, *, project, environment_row) -> dict[str, Any]:
    """Crawl one (project, environment) pair end to end. Never raises —
    every internal failure degrades to a logged, partial, or empty result
    dict, matching every other Project Intelligence worker entry point.
    Caller (workers/tasks/pi_crawl.py) owns the transaction boundary
    (commits after screen/component extraction, then again after vision
    extraction, so a vision failure never rolls back the screens/components
    that were already safely captured).
    """
    from app.services import pi_extract
    from app.services import pi_ingest as pi_ingest_svc

    stats: dict[str, Any] = {
        "status": "skipped", "extraction": {}, "screenshots_analyzed": 0,
        "design_patterns_new": 0,
    }

    ctx = _resolve_crawl_context(db, project=project, environment_row=environment_row)
    if ctx is None:
        stats["reason"] = "no resolvable start URL, login, or domain guardrail"
        return stats

    known_routes = _known_routes(db, project_id=project.id)
    goal = _build_crawl_goal(ctx.environment_url, known_routes)
    crawl_run_id = uuid.uuid4()

    from app.services.ai_runner import run_ai_test_sync

    logger.info(
        "pi_crawl: starting crawl %s for project %s environment %s (%s), "
        "%d known route(s)",
        crawl_run_id, project.id, environment_row.environment,
        ctx.environment_url, len(known_routes),
    )

    try:
        result = run_ai_test_sync(
            goal=goal,
            environment_url=ctx.environment_url,
            allowed_domains=ctx.allowed_domains,
            sensitive_data=ctx.sensitive_data,
            cookies=ctx.cookies,
            max_steps=pi_ingest_svc.crawl_max_steps(),
            max_duration_s=pi_ingest_svc.crawl_max_duration_s(),
            enable_live_capture=False,
        )
    except Exception:
        logger.exception(
            "pi_crawl: run_ai_test_sync failed for project %s environment %s",
            project.id, environment_row.environment,
        )
        stats["status"] = "error"
        return stats

    stats["status"] = result.get("status") or "unknown"

    # ── 1. Screen/component/nav extraction — reuses the exact Phase 1/2
    #    pipeline every RF/Vibe capture already goes through. ────────────
    try:
        screens = pi_ingest_svc.normalize_vibe_history(result.get("history_json"))
        if screens:
            event = pi_ingest_svc.write_capture_event(
                db, project_id=project.id, source_type="crawl", source_run_id=crawl_run_id,
                payload_json={"environment_id": str(environment_row.id), "screens": screens},
            )
            if event is not None:
                stats["extraction"] = pi_extract.process_capture_event(db, event)
                db.commit()

                # Same "only propose a new flow when the graph actually
                # changed" guard pi_ingest.py's own _maybe_propose_flow
                # applies — kept inline here (5 lines) rather than importing
                # a private helper across task modules.
                changed = stats["extraction"].get("screens_new") or stats["extraction"].get("edges_new")
                if changed:
                    try:
                        from app.services import pi_flow

                        pi_flow.propose_model(
                            db, project_id=project.id, environment_id=environment_row.id,
                            generated_from_run_ids=[str(crawl_run_id)],
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                        logger.warning(
                            "pi_crawl: flow proposal failed for project %s (crawl %s)",
                            project.id, crawl_run_id, exc_info=True,
                        )
    except Exception:
        db.rollback()
        logger.exception(
            "pi_crawl: screen/component extraction failed for crawl %s", crawl_run_id,
        )

    # ── 2. Vision design-pattern extraction — independent of #1 above; a
    #    failure here must not undo the screens/components already
    #    committed. ───────────────────────────────────────────────────────
    if pi_ingest_svc.design_extraction_enabled():
        try:
            shots = _sample_screenshots(result.get("events") or [], pi_ingest_svc.crawl_max_screenshots())
            for i, b64 in enumerate(shots):
                path = _save_screenshot(project.id, environment_row.id, crawl_run_id, i, b64)
                if path is None:
                    continue
                stats["screenshots_analyzed"] += 1
                stats["design_patterns_new"] += _analyze_screenshot(
                    db, project_id=project.id, screenshot_path=path, screenshot_b64=b64,
                )
            if shots:
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "pi_crawl: vision extraction failed for crawl %s", crawl_run_id,
            )

    logger.info(
        "pi_crawl: crawl %s finished (status=%s) — %s, %d screenshot(s) analyzed, "
        "%d design pattern(s)",
        crawl_run_id, stats["status"], stats["extraction"],
        stats["screenshots_analyzed"], stats["design_patterns_new"],
    )
    return stats


# ── Retention cleanup (spec §24: "An artifact retention policy is required
#    before Phase 3 ships, together with its cleanup task") ─────────────────

def cleanup_expired_artifacts(db) -> dict[str, int]:
    """Delete crawl screenshot files older than
    pi_ingest.artifact_retention_days(), then null out evidence_ref on any
    pi_design_patterns row whose file is gone. The knowledge row itself
    (pattern_type/value/description) is never deleted by this — only its
    pointer to a screenshot that no longer exists, matching spec §16's
    "referenced, never duplicated" framing: the pointer expires, the fact
    does not."""
    from app.models.project_intelligence import PiDesignPattern
    from app.services import pi_ingest as pi_ingest_svc

    retention_days = pi_ingest_svc.artifact_retention_days()
    cutoff_s = retention_days * 86400
    now = time.time()

    checked = 0
    deleted = 0
    root = _screenshot_root()

    if os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                path = os.path.join(dirpath, fname)
                checked += 1
                try:
                    age_s = now - os.path.getmtime(path)
                    if age_s > cutoff_s:
                        os.remove(path)
                        deleted += 1
                except OSError:
                    logger.warning("pi_crawl: could not stat/delete %s", path, exc_info=True)

    cleared = 0
    try:
        rows = (
            db.query(PiDesignPattern)
            .filter(PiDesignPattern.evidence_ref.isnot(None))
            .all()
        )
        for row in rows:
            if row.evidence_ref and not os.path.isfile(row.evidence_ref):
                row.evidence_ref = None
                cleared += 1
        if cleared:
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("pi_crawl: failed to clear stale evidence_ref pointers")

    logger.info(
        "pi_crawl: retention sweep (%d day(s)) — checked %d file(s), deleted %d, "
        "cleared %d evidence_ref pointer(s)",
        retention_days, checked, deleted, cleared,
    )
    return {"checked": checked, "deleted": deleted, "evidence_refs_cleared": cleared}
