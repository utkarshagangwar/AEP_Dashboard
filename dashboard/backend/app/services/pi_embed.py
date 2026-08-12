"""Project Intelligence — Semantic Search (Phase 5 / "Scale", spec §16
table 8 row for pi_embeddings, table 17 Phase 5, table 18 Q10).

Generates and stores vector embeddings for verified PiBehaviorNote rows —
the one entity type spec's own comparison table calls out by name
("Behaviour Notes: Postgres full-text search in Phases 1-4; semantic
search only after pgvector") — and serves cosine-similarity search over
them as a richer alternative to the plain ILIKE search
api/v1/project_intelligence.py's behavior-notes list endpoint already has.

INFRASTRUCTURE DEPENDENCY, honestly flagged. This is the first Project
Intelligence file whose functioning depends on something outside pure,
always-available application code:

  1. The `vector` Postgres EXTENSION binary (migration 0053 runs
     `CREATE EXTENSION IF NOT EXISTS vector`, which fails loudly at
     deploy time if that binary isn't installed on the DB server).
  2. The `pgvector` PYTHON package (requirements.txt) for the SQLAlchemy
     column type and its comparator methods.

Every function below checks pi_ingest.semantic_search_enabled() first —
which itself checks BOTH the PI_SEMANTIC_SEARCH_ENABLED flag AND that
PiEmbedding actually got defined (see models/project_intelligence.py's
defensive import) — and no-ops/returns cleanly if either is missing. A
deployment that has not yet installed pgvector, or has not yet run
migration 0053, or simply hasn't turned the flag on, sees ZERO behavior
change and zero import-time failure from this file existing.

FEATURE-FLAGGED, master-switch convention, default OFF:
PI_SEMANTIC_SEARCH_ENABLED — its own switch, deliberately separate from
PI_ENABLED / PI_CONTEXT_ENABLED / PI_CRAWL_ENABLED, confirmed directly
with the project owner at Phase 5 kickoff rather than inferred: every
phase's rollout stays opt-in, nothing broadens silently.

EMBEDDING MODEL: Google's text-embedding-004 (768 dimensions), via
`langchain-google-genai`'s GoogleGenerativeAIEmbeddings — reuses the
langchain-google-genai==2.1.2 dependency this repo already pins for
Hands' own LLM calls (ai_runner.py) rather than adding a second Google
SDK, and reuses the same GEMINI_API_KEY / GOOGLE_API_KEY(S) precedence
llm_router.py already establishes (see _resolve_api_key() below) rather
than inventing a third place to configure the same credential.

No multi-key rotation across configured keys, unlike ai_runner.py's Hands
loop: this is a low-volume, best-effort background job (embedding a
handful of verified notes at a time, or answering one search query), not
a 20-minute live browser session that must not die mid-run. A transient
single-key failure just skips that note for now — the nightly backfill
task (workers/tasks/pi_embed.py) picks it up on its next pass.

ONLY VERIFIED KNOWLEDGE IS EVER EMBEDDED — same rule as pi_context.py's
brief and every other Phase 1-4 read surface: a PiBehaviorNote is only
ever embedded once status='verified'. Editing or re-verifying a note
re-embeds it (content_hash detects the text changed); a note that is
rejected or later edited back to pending is NOT automatically stripped of
its existing embedding — see delete_stale_embeddings()'s docstring for
why that cleanup is a deliberate, separate, idempotent pass (run by the
same nightly backfill task) rather than something every write path must
remember to do.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

from app.core.logging import get_logger
from app.services import pi_ingest

logger = get_logger(__name__)

_EMBEDDING_MODEL = "models/text-embedding-004"
_EMBEDDING_DIM = 768


def _resolve_api_key() -> Optional[str]:
    """Same precedence llm_router._validate_keys_present() already
    establishes for Hands' own Gemini calls — GEMINI_API_KEY first, then
    GOOGLE_API_KEY, then the first entry of the plural GOOGLE_API_KEYS
    rotation list. Reused rather than reinvented so there is exactly one
    place in this codebase describing "how do we find a Google API key,"
    not two that could drift apart."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if key:
        return key
    plural = os.environ.get("GOOGLE_API_KEYS", "")
    if plural.strip():
        first = plural.split(",")[0].strip()
        if first:
            return first
    return None


def _get_embeddings_client():
    """Returns a configured GoogleGenerativeAIEmbeddings instance, or None
    if no key is configured. Built fresh per call rather than cached at
    module level — this is a low-frequency background operation, not a hot
    path, so the (small) cost of re-constructing it is not worth adding
    module-level mutable state for."""
    api_key = _resolve_api_key()
    if not api_key:
        logger.warning(
            "pi_embed: no GEMINI_API_KEY/GOOGLE_API_KEY(S) configured — "
            "cannot generate embeddings."
        )
        return None
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=_EMBEDDING_MODEL, google_api_key=api_key)
    except Exception:
        logger.warning("pi_embed: failed to construct embeddings client", exc_info=True)
        return None


def _content_hash(text: str) -> str:
    """Same purpose/shape as PiScreen.content_hash — sha256 hex digest,
    used to skip re-embedding a note whose text hasn't actually changed
    since it was last embedded (embedding calls are the expensive part of
    this feature, both in latency and in cost)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_behavior_note(db, *, note_id) -> bool:
    """Embed one verified PiBehaviorNote, upserting its pi_embeddings row.

    Returns False (a no-op, never an exception) when: semantic search is
    disabled, the note doesn't exist, the note isn't status='verified'
    (pending/rejected/superseded notes are never embedded — same
    "verified only" rule as pi_context.py), its text is empty, no API key
    is configured, or the embedding call itself fails. Returns True only
    on an actual successful write.
    """
    if not pi_ingest.semantic_search_enabled():
        return False
    try:
        from app.models.project_intelligence import PiBehaviorNote, PiEmbedding, PiStatus

        note = db.get(PiBehaviorNote, note_id)
        if note is None or note.status != PiStatus.verified:
            return False
        text = (note.description or "").strip()
        if not text:
            return False

        new_hash = _content_hash(text)
        existing = (
            db.query(PiEmbedding)
            .filter(PiEmbedding.entity_type == "behavior_note", PiEmbedding.entity_id == note.id)
            .one_or_none()
        )
        if existing is not None and existing.content_hash == new_hash:
            return False  # already up to date, nothing to do

        client = _get_embeddings_client()
        if client is None:
            return False
        vector = client.embed_documents([text])[0]
        if len(vector) != _EMBEDDING_DIM:
            logger.warning(
                "pi_embed: embedding model returned %d dimensions, expected %d — "
                "skipping write for note %s (model/column mismatch — check "
                "_EMBEDDING_MODEL against migration 0053's dimension).",
                len(vector), _EMBEDDING_DIM, note_id,
            )
            return False

        if existing is not None:
            existing.embedding = vector
            existing.content_hash = new_hash
        else:
            db.add(
                PiEmbedding(
                    project_id=note.project_id, entity_type="behavior_note",
                    entity_id=note.id, content_hash=new_hash, embedding=vector,
                )
            )
        db.commit()
        return True
    except Exception:
        logger.warning(
            "pi_embed: embed_behavior_note failed for note %s", note_id, exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False


def delete_stale_embeddings(db, *, project_id=None, limit: int = 500) -> int:
    """Removes pi_embeddings rows whose source PiBehaviorNote is gone, or
    is no longer status='verified' (edited back to pending, or rejected).

    A SEPARATE, IDEMPOTENT PASS rather than something every write path
    must remember to trigger: embed_behavior_note() is the only writer of
    this table, and it only ever runs on the review-action approval path
    (api/v1/project_intelligence.py) plus this module's own nightly
    backfill task — neither of those is the right place to also know
    about every way a note can stop being verified (edited back to
    pending, rejected, its screen cascade-deleted, ...). Running this
    regularly from the same Celery Beat task that calls
    backfill_missing_embeddings() below keeps the table correct without
    every unrelated code path needing to remember a cleanup step.

    Returns the number of rows deleted. Fail-open: any error returns 0
    rather than raising.
    """
    if not pi_ingest.semantic_search_enabled():
        return 0
    try:
        from app.models.project_intelligence import PiBehaviorNote, PiEmbedding, PiStatus

        query = db.query(PiEmbedding).filter(PiEmbedding.entity_type == "behavior_note")
        if project_id is not None:
            query = query.filter(PiEmbedding.project_id == project_id)
        rows = query.limit(limit).all()

        note_ids = {r.entity_id for r in rows}
        verified_ids = set()
        if note_ids:
            verified_ids = {
                n.id
                for n in db.query(PiBehaviorNote.id).filter(
                    PiBehaviorNote.id.in_(note_ids), PiBehaviorNote.status == PiStatus.verified,
                ).all()
            }

        deleted = 0
        for row in rows:
            if row.entity_id not in verified_ids:
                db.delete(row)
                deleted += 1
        if deleted:
            db.commit()
        return deleted
    except Exception:
        logger.warning("pi_embed: delete_stale_embeddings failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def backfill_missing_embeddings(db, *, project_id=None, limit: int = 100) -> int:
    """Embeds up to `limit` verified PiBehaviorNote rows that don't have a
    current embedding yet — catches up after PI_SEMANTIC_SEARCH_ENABLED is
    first turned on for existing data, and covers any note that became
    verified through a path that didn't call embed_behavior_note()
    directly (e.g. a direct SQL edit, or a future write path this file
    doesn't know about yet). Returns the number successfully embedded.
    """
    if not pi_ingest.semantic_search_enabled():
        return 0
    try:
        from app.models.project_intelligence import PiBehaviorNote, PiEmbedding, PiStatus

        embedded_ids_q = db.query(PiEmbedding.entity_id).filter(
            PiEmbedding.entity_type == "behavior_note"
        )
        if project_id is not None:
            embedded_ids_q = embedded_ids_q.filter(PiEmbedding.project_id == project_id)
        embedded_ids = {r[0] for r in embedded_ids_q.all()}

        query = db.query(PiBehaviorNote).filter(PiBehaviorNote.status == PiStatus.verified)
        if project_id is not None:
            query = query.filter(PiBehaviorNote.project_id == project_id)
        if embedded_ids:
            query = query.filter(PiBehaviorNote.id.notin_(embedded_ids))

        notes = query.order_by(PiBehaviorNote.updated_at.desc()).limit(limit).all()
        count = 0
        for note in notes:
            if embed_behavior_note(db, note_id=note.id):
                count += 1
        return count
    except Exception:
        logger.warning("pi_embed: backfill_missing_embeddings failed", exc_info=True)
        return 0


def semantic_search_behavior_notes(db, *, project_id, query_text: str, limit: int = 10) -> list:
    """Cosine-similarity search over verified, embedded PiBehaviorNote
    rows for this project. Returns a list of PiBehaviorNote ORM rows,
    nearest first — same return shape as a normal .all() query, so the API
    layer can serialize it with the existing PiBehaviorNoteOut schema
    unchanged.

    Returns [] (never raises) when semantic search is disabled, the query
    text is empty, no API key is configured, or the embedding call fails —
    the caller (api/v1/project_intelligence.py) falls back to the existing
    ILIKE search in that case, so a transient embedding-API outage
    degrades search quality, never search availability.
    """
    if not pi_ingest.semantic_search_enabled():
        return []
    text = (query_text or "").strip()
    if not text or project_id is None:
        return []
    try:
        from app.models.project_intelligence import PiBehaviorNote, PiEmbedding, PiStatus

        client = _get_embeddings_client()
        if client is None:
            return []
        # task_type="retrieval_query" vs. embed_documents()'s implicit
        # "retrieval_document" default — text-embedding-004 supports this
        # asymmetric mode natively and it measurably improves search
        # relevance over embedding the query the same way as the stored
        # documents; a one-line, real quality gain the API already offers,
        # not a simplification skipped for effort.
        client.task_type = "retrieval_query"
        query_vector = client.embed_query(text)

        rows = (
            db.query(PiBehaviorNote)
            .join(PiEmbedding, PiEmbedding.entity_id == PiBehaviorNote.id)
            .filter(
                PiEmbedding.entity_type == "behavior_note",
                PiBehaviorNote.project_id == project_id,
                PiBehaviorNote.status == PiStatus.verified,
            )
            .order_by(PiEmbedding.embedding.cosine_distance(query_vector))
            .limit(limit)
            .all()
        )
        return rows
    except Exception:
        logger.warning(
            "pi_embed: semantic_search_behavior_notes failed for project %s",
            project_id, exc_info=True,
        )
        return []
