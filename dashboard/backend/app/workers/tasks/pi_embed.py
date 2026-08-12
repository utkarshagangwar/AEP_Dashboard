"""Celery tasks — Project Intelligence Phase 5 (Semantic Search).

Two scheduled, project-agnostic maintenance passes over pi_embeddings —
there is no per-event "embed this note right now" task here, unlike
Phase 1's ingest tasks or Phase 3's crawl tasks, because embedding a note
the moment it's approved is already handled synchronously (fire-and-
forget, same pattern as every other post-approval hook) from
api/v1/project_intelligence.py's review-action endpoint. These two tasks
exist to catch everything that synchronous hook can't: notes verified
before the feature was turned on, and notes that stop being verified
after they were already embedded.

Both are gated purely on pi_ingest.semantic_search_enabled() — checked
inside each task, not just via Celery Beat's schedule, so a manually
triggered run (`.delay()`) or a stale Beat schedule from before the
feature was disabled again both still no-op correctly.
"""
from app.core.logging import get_logger
from app.services import pi_ingest
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="workers.tasks.pi_embed.embed_one_note",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def embed_one_note(self, note_id: str) -> dict:
    """Fire-and-forget, queued from api/v1/project_intelligence.py's
    apply_review_action() the moment a behavior note is approved/edited
    into status='verified' — same "queue it after the triggering write is
    already committed, never awaited" pattern as
    ai_execution.run_ai_test_task's ingest_vibe_capture.delay(...) call.
    A failure to queue this, or a failure inside it, can never affect the
    review action it followed; the nightly backfill task below is the
    retry net for anything this misses."""
    if not pi_ingest.semantic_search_enabled():
        return {"skipped": True}

    from app.core.database import SessionLocal
    from app.services import pi_embed

    db = SessionLocal()
    try:
        embedded = pi_embed.embed_behavior_note(db, note_id=note_id)
        return {"embedded": embedded}
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.pi_embed.backfill_embeddings",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def backfill_embeddings(self) -> dict:
    """Nightly: embeds any verified PiBehaviorNote rows across every
    project that don't have a current embedding yet. See
    services/pi_embed.py:backfill_missing_embeddings() for why this needs
    to exist alongside the synchronous per-approval embed call."""
    if not pi_ingest.semantic_search_enabled():
        logger.info("pi_embed: semantic search is disabled — skipping backfill")
        return {"skipped": True}

    from app.core.database import SessionLocal
    from app.models.project_intelligence import PiScreen
    from app.services import pi_embed

    db = SessionLocal()
    total = 0
    try:
        # One project at a time, capped per project, rather than one
        # unbounded global query — mirrors pi_crawl.py's per-project
        # eligibility loop, and keeps a single very active project from
        # starving every other project's backfill within one nightly run.
        project_ids = [
            row[0]
            for row in db.query(PiScreen.project_id).distinct().all()
        ]
        for project_id in project_ids:
            try:
                total += pi_embed.backfill_missing_embeddings(db, project_id=project_id, limit=100)
            except Exception:
                logger.warning(
                    "pi_embed: backfill failed for project %s", project_id, exc_info=True,
                )
        logger.info("pi_embed: backfill embedded %d note(s) across %d project(s)",
                    total, len(project_ids))
        return {"embedded": total, "projects_checked": len(project_ids)}
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.pi_embed.cleanup_stale_embeddings",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def cleanup_stale_embeddings(self) -> dict:
    """Nightly: removes embeddings whose source note is no longer
    verified. See services/pi_embed.py:delete_stale_embeddings()."""
    if not pi_ingest.semantic_search_enabled():
        logger.info("pi_embed: semantic search is disabled — skipping cleanup")
        return {"skipped": True}

    from app.core.database import SessionLocal
    from app.services import pi_embed

    db = SessionLocal()
    try:
        deleted = pi_embed.delete_stale_embeddings(db, limit=1000)
        logger.info("pi_embed: cleanup removed %d stale embedding(s)", deleted)
        return {"deleted": deleted}
    finally:
        db.close()
