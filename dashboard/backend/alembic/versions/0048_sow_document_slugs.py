"""SOW: slug-based document URLs

Adds `sow_documents.slug` (the current canonical URL identifier) and a new
`sow_document_slug_history` table (every slug a document has ever held).

Why a separate history table rather than just a column on sow_documents:
renaming a document rotates it onto a new slug, and the old one must keep
resolving -- both to redirect an old bookmark to the live URL, and, more
importantly, to make sure a *different* document can never later claim that
old slug. sow_document_slug_history.slug is UNIQUE across the whole table
(not scoped to one document), so once a slug has been used, ever, by anyone,
it can never be assigned to a different document again. See that model's
docstring in app/models/sow.py for the full reasoning.

Backfill: every existing sow_documents row gets a slug derived from its
title, processed oldest-created-first so ties resolve in creation order
(the older document keeps the "cleaner" slug, a later duplicate title gets
the -2/-3 suffix) -- same collision rule the live create/rename endpoints
use afterwards. Every backfilled slug also gets its own history row so it
is immediately resolvable through the same lookup path a freshly created
document uses.

Revision ID: 0048_sow_document_slugs
Revises: 0047_backfill_ai_usage_cost
Create Date: 2026-08-09
"""
import re
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_sow_document_slugs"
down_revision: Union[str, None] = "0047_backfill_ai_usage_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helpers (matches 0028's established convention) ────────────

def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": index_name}
    )
    return result.fetchone() is not None


# ── Slugify -- deliberately duplicated from api/v1/sow.py rather than
#    imported. Migrations must keep working forever even after the app code
#    that wrote them changes or is deleted; this is ~6 lines and cheap to
#    keep frozen here. Must stay byte-for-byte equivalent in behaviour to
#    the live version (same collapse-to-hyphen, same lowercase, same 80-char
#    cap) so a backfilled slug looks exactly like one the live code would
#    have generated for the same title.
def _slugify(title: str) -> str:
    text = (title or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:80] or "sow"


def upgrade() -> None:
    # ── sow_documents.slug (nullable first -- backfilled below, then locked
    #    down to NOT NULL + UNIQUE once every row has a value) ─────────────
    if not _column_exists("sow_documents", "slug"):
        op.add_column("sow_documents", sa.Column("slug", sa.String(120), nullable=True))

    # ── sow_document_slug_history ───────────────────────────────────────────
    if not _table_exists("sow_document_slug_history"):
        op.create_table(
            "sow_document_slug_history",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("slug", sa.String(120), nullable=False),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_sow_document_slug_history_document_id",
            "sow_document_slug_history",
            ["document_id"],
        )
        op.create_index(
            "ix_sow_document_slug_history_slug_unique",
            "sow_document_slug_history",
            ["slug"],
            unique=True,
        )

    # ── Backfill ─────────────────────────────────────────────────────────────
    # Slugs already claimed (by a previous partial run of this migration, or
    # by history rows this pass is about to add) -- checked in Python rather
    # than a DB round-trip per row, since the whole document table is small
    # enough to hold in memory and this only ever runs once per deploy.
    bind = op.get_bind()
    used = {
        row[0]
        for row in bind.execute(sa.text("SELECT slug FROM sow_document_slug_history"))
    }
    rows = bind.execute(
        sa.text(
            "SELECT id, title FROM sow_documents "
            "WHERE slug IS NULL ORDER BY created_at ASC, id ASC"
        )
    ).fetchall()
    for doc_id, title in rows:
        base = _slugify(title)
        candidate = base
        n = 2
        while candidate in used:
            suffix = f"-{n}"
            candidate = f"{base[: 80 - len(suffix)]}{suffix}"
            n += 1
        used.add(candidate)
        bind.execute(
            sa.text("UPDATE sow_documents SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": doc_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO sow_document_slug_history (id, document_id, slug, created_at) "
                "VALUES (:hid, :did, :slug, now())"
            ),
            {"hid": str(uuid.uuid4()), "did": doc_id, "slug": candidate},
        )

    # ── Lock down: every row now has a slug, so this can be enforced from
    #    here on exactly like the live create/rename endpoints assume ───────
    op.alter_column(
        "sow_documents", "slug", existing_type=sa.String(120), nullable=False
    )
    if not _index_exists("ix_sow_documents_slug_unique"):
        op.create_index(
            "ix_sow_documents_slug_unique", "sow_documents", ["slug"], unique=True
        )


def downgrade() -> None:
    op.drop_index("ix_sow_documents_slug_unique", table_name="sow_documents")
    op.drop_column("sow_documents", "slug")
    op.drop_table("sow_document_slug_history")
