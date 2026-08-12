"""Phase 5 — pgvector extension + pi_embeddings table.

See AEP_Project_Intelligence_Consolidated_Spec (v3.0) §16 table 8
("pi_embeddings | id, project_id, entity_type, entity_id, embedding |
Semantic search — blocked on pgvector | 5"), table 17 Phase 5 ("pgvector +
embeddings + semantic search"), and table 18 Q10 ("Can pgvector be added
to the Postgres image? ... Assume yes; Phases 1-4 do not depend on it").

INFRASTRUCTURE, not purely additive application code — the one way this
migration differs from every prior Project Intelligence migration. Every
migration through 0052 only ever added tables/columns that any ordinary
Postgres 13+ instance already supports. This one requires the `vector`
extension BINARY to already be installed on whatever Postgres instance
DATABASE_URL points to (the `pgvector`/`postgresql-<NN>-pgvector` OS
package on a self-hosted instance, or the equivalent toggle on a managed
Postgres offering that bundles it). `CREATE EXTENSION IF NOT EXISTS
vector` below is deliberately NOT wrapped in a try/except — if the
extension binary isn't present, this migration fails loudly, at deploy
time, which is exactly the behavior wanted: a missing extension surfaces
as a clear migration error an operator sees immediately, never as a
silent runtime failure discovered later when a query hits a type that
doesn't exist.

Everything downstream of this table is still additive and fail-open in
the usual sense: app/models/project_intelligence.py's PiEmbedding model is
guarded by a defensive import (falls back to None, not an ImportError, if
the `pgvector` PYTHON package isn't installed), and every function in
app/services/pi_embed.py checks that guard plus a dedicated feature flag
(PI_SEMANTIC_SEARCH_ENABLED, default off) before touching this table at
all. See that module's docstring for the full picture.

VECTOR DIMENSION: 768, matching Google's text-embedding-004 output size
(spec §28's Phase 5 kickoff answer: Google, matching this codebase's
existing default LLM provider). If a future embedding model with a
different dimension is adopted, that is a new migration (a vector
column's dimension is fixed at creation), not a change to this one.

INDEX: HNSW, not IVFFlat — chosen because IVFFlat's recommended `lists`
parameter is tuned from the table's expected row count in advance and its
lists must be relatively evenly distributed for quality/recall to hold,
both awkward assumptions for a table that starts EMPTY and grows slowly
(this is a per-project pilot feature, not a bulk-loaded corpus).
HNSW builds incrementally with no such training-set assumption and pays
its cost in write-time (still cheap at this table's expected scale) and
memory rather than requiring a re-tune as data grows. Both are pgvector
built-ins from 0.5.0+ (see requirements.txt's pgvector==0.5.0 pin);
cosine distance is used throughout since embedding similarity comparisons
are the standard cosine-similarity use case for text embeddings.

Idempotent, mirroring the `_table_exists`/`_column_exists` convention
used by every migration since 0049.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0053_pi_embeddings"
down_revision = "0052_pi_crawl"
branch_labels = None
depends_on = None

_EMBEDDING_DIM = 768


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def _extension_exists(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = :name"), {"name": name}
    ).first()
    return row is not None


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    # Deliberately not idempotency-guarded beyond IF NOT EXISTS itself —
    # see module docstring for why a missing extension binary should fail
    # this migration loudly rather than be caught and swallowed.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if not _table_exists("pi_embeddings"):
        op.create_table(
            "pi_embeddings",
            sa.Column(
                "id", UUID(as_uuid=True), primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "project_id", UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
            ),
            # Polymorphic, no FK — same convention as pi_change_log.entity_id
            # / pi_review_actions.entity_id (migration 0049's docstring).
            # 'behavior_note' is the only value written by Phase 5's own
            # code (spec's explicit Behaviour-Notes-semantic-search call
            # out); the column stays generic so 'screen'/'component' can be
            # added later without a schema change.
            sa.Column("entity_type", sa.String(30), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
            # Detects a stale embedding needing regeneration after the
            # source text changes — same purpose as PiScreen.content_hash.
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at", sa.DateTime(), server_default=sa.func.now(),
                onupdate=sa.func.now(), nullable=False,
            ),
            sa.UniqueConstraint(
                "entity_type", "entity_id", name="uq_pi_embeddings_entity"
            ),
        )
        # The embedding column itself is added via raw SQL rather than
        # sa.Column(...) above: the `vector` type is only registered with
        # SQLAlchemy's dialect machinery when app code imports
        # pgvector.sqlalchemy.Vector (see models/project_intelligence.py),
        # which this standalone migration script does not do — importing
        # the app's model layer from inside a migration is exactly the
        # kind of coupling Alembic's own docs warn against, and every
        # prior migration in this codebase (0049-0052) already avoids it.
        op.execute(
            f"ALTER TABLE pi_embeddings ADD COLUMN embedding vector({_EMBEDDING_DIM}) NOT NULL"
        )
        op.execute(
            "CREATE INDEX ix_pi_embeddings_project_id ON pi_embeddings (project_id)"
        )
        op.execute(
            "CREATE INDEX ix_pi_embeddings_entity_type ON pi_embeddings (entity_type)"
        )

    if not _index_exists("pi_embeddings", "ix_pi_embeddings_embedding_hnsw"):
        op.execute(
            "CREATE INDEX ix_pi_embeddings_embedding_hnsw ON pi_embeddings "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    if _index_exists("pi_embeddings", "ix_pi_embeddings_embedding_hnsw"):
        op.execute("DROP INDEX IF EXISTS ix_pi_embeddings_embedding_hnsw")
    if _table_exists("pi_embeddings"):
        op.drop_table("pi_embeddings")
    # The `vector` extension is deliberately NOT dropped on downgrade —
    # another database/schema on the same Postgres instance may depend on
    # it, and DROP EXTENSION has no "IF NOT USED ELSEWHERE" safety net.
    # Uninstalling the extension binary/toggle is an operator decision,
    # not something this migration should do unilaterally.
