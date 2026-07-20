"""SOW Creation & Rewrite — Phase 0 foundation

Adds the full schema needed for the SOW authoring feature (see
SOW_FEATURE_PLAN.md at the repo root for the design):

  sow_documents             one row per SOW document a user is authoring
  sow_document_versions     every full-generation or patch version of a
                             document, versioned and never mutated in place
  sow_sections              structured content per section of a version
                             (content_blocks JSONB — see plan §11.6)
  sow_requirements_ledger   extracted facts/UI-element inventory a version's
                             sections are drafted from and audited against
  sow_generation_jobs       Celery job progress/status per version

Also extends the existing `artifact_type_enum` (design_artifacts) with two
new source types this feature ingests: 'meeting_transcript' and
'meeting_recording'. No new columns on design_artifacts are needed — these
reuse existing columns (platform_name, storage_path, sha256 dedupe) exactly
as the 'video' type already does.

This migration ships the full Phase 0 schema in one shot, including the
status/error/human-edit-protection columns from the plan's Hardening
Addendum (§11.12) — intentionally not shipping a weaker first pass and
migrating again immediately after.

Revision ID: 0028_sow_foundation
Revises: 0027_android_platform
Create Date: 2026-07-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_sow_foundation"
down_revision: Union[str, None] = "0027_android_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Idempotency helpers (matches 0013/0021's established convention) ────────

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


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def _enum_value_exists(enum_name: str, value: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = :enum_name AND e.enumlabel = :value"
        ),
        {"enum_name": enum_name, "value": value},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # ── Extend the existing artifact_type_enum (design_artifacts reuse) ─────
    for value in ("meeting_transcript", "meeting_recording"):
        if not _enum_value_exists("artifact_type_enum", value):
            op.execute(f"ALTER TYPE artifact_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    # ── New enums (raw SQL so SQLAlchemy never auto-emits CREATE TYPE) ──────
    if not _enum_exists("sow_document_status_enum"):
        op.execute(
            "CREATE TYPE sow_document_status_enum AS ENUM "
            "('draft', 'generating', 'ready', 'error')"
        )
    if not _enum_exists("sow_version_kind_enum"):
        op.execute(
            "CREATE TYPE sow_version_kind_enum AS ENUM ('full_generation', 'patch')"
        )
    if not _enum_exists("sow_version_status_enum"):
        op.execute(
            "CREATE TYPE sow_version_status_enum AS ENUM "
            "('pending', 'generating', 'done', 'done_with_errors', 'error')"
        )
    if not _enum_exists("sow_section_status_enum"):
        op.execute(
            "CREATE TYPE sow_section_status_enum AS ENUM "
            "('pending', 'generating', 'done', 'error')"
        )
    if not _enum_exists("sow_job_stage_enum"):
        op.execute(
            "CREATE TYPE sow_job_stage_enum AS ENUM "
            "('ledger_extraction', 'drafting', 'audit', 'assembly')"
        )
    if not _enum_exists("sow_job_status_enum"):
        op.execute(
            "CREATE TYPE sow_job_status_enum AS ENUM "
            "('queued', 'running', 'done', 'done_with_errors', 'error')"
        )
    if not _enum_exists("sow_ledger_fact_type_enum"):
        op.execute(
            "CREATE TYPE sow_ledger_fact_type_enum AS ENUM "
            "('feature', 'decision', 'ui_element', 'open_question')"
        )
    if not _enum_exists("sow_ui_element_type_enum"):
        op.execute(
            "CREATE TYPE sow_ui_element_type_enum AS ENUM "
            "('button', 'dropdown', 'filter', 'checkbox', 'toggle', 'slider', "
            "'three_dot_menu', 'tab', 'modal', 'other')"
        )

    # ── Table 1: sow_documents ───────────────────────────────────────────────
    # current_version_id references sow_document_versions, which itself
    # references this table -- created without the FK first, added after
    # sow_document_versions exists (see bottom of this function).
    if not _table_exists("sow_documents"):
        op.create_table(
            "sow_documents",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "draft", "generating", "ready", "error",
                    name="sow_document_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="draft",
            ),
            # Soft delete -- matches the existing Project.is_active convention
            # (app/models/project.py). SOW documents are AI-generated work
            # product that can represent significant spend/time to
            # regenerate; DELETE hides rather than destroys, same as
            # projects. A hard-delete admin path can be added later if
            # actually needed -- not exposed in Phase 0.
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(),
                onupdate=sa.func.now(), nullable=False,
            ),
        )
        op.create_index("ix_sow_documents_project_id", "sow_documents", ["project_id"])
        op.create_index("ix_sow_documents_status", "sow_documents", ["status"])
        op.create_index("ix_sow_documents_is_active", "sow_documents", ["is_active"])

    # ── Table 2: sow_document_versions ──────────────────────────────────────
    if not _table_exists("sow_document_versions"):
        op.create_table(
            "sow_document_versions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer, nullable=False),
            sa.Column(
                "kind",
                postgresql.ENUM(
                    "full_generation", "patch",
                    name="sow_version_kind_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "parent_version_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_document_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "generating", "done", "done_with_errors", "error",
                    name="sow_version_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("generated_by_model", sa.String(200), nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_sow_document_versions_document_id", "sow_document_versions", ["document_id"]
        )
        op.create_index(
            "ix_sow_document_versions_doc_version_unique",
            "sow_document_versions",
            ["document_id", "version_number"],
            unique=True,
        )

    # Now that sow_document_versions exists, wire up sow_documents.current_version_id
    if _table_exists("sow_documents") and _table_exists("sow_document_versions"):
        bind = op.get_bind()
        has_fk = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'sow_documents' "
                "AND constraint_name = 'fk_sow_documents_current_version_id'"
            )
        ).fetchone()
        if not has_fk:
            op.create_foreign_key(
                "fk_sow_documents_current_version_id",
                "sow_documents",
                "sow_document_versions",
                ["current_version_id"],
                ["id"],
                ondelete="SET NULL",
            )

    # ── Table 3: sow_sections ───────────────────────────────────────────────
    if not _table_exists("sow_sections"):
        op.create_table(
            "sow_sections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "version_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_document_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("order_index", sa.Integer, nullable=False),
            sa.Column("heading", sa.String(500), nullable=False),
            # Stable across regenerations -- see plan §11.4. Not globally
            # unique on its own; unique together with version_id below.
            sa.Column("section_key", sa.String(200), nullable=False),
            sa.Column(
                "content_blocks", postgresql.JSONB, nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "schema_version", sa.Integer, nullable=False, server_default="1"
            ),
            sa.Column("coverage_score", sa.Integer, nullable=True),
            sa.Column("coverage_gaps", postgresql.JSONB, nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "generating", "done", "error",
                    name="sow_section_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column(
                "edited_by_human", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column("edited_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(),
                onupdate=sa.func.now(), nullable=False,
            ),
        )
        op.create_index("ix_sow_sections_version_id", "sow_sections", ["version_id"])
        op.create_index(
            "ix_sow_sections_version_key_unique",
            "sow_sections",
            ["version_id", "section_key"],
            unique=True,
        )

    # ── Table 4: sow_requirements_ledger ────────────────────────────────────
    if not _table_exists("sow_requirements_ledger"):
        op.create_table(
            "sow_requirements_ledger",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "fact_type",
                postgresql.ENUM(
                    "feature", "decision", "ui_element", "open_question",
                    name="sow_ledger_fact_type_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "element_type",
                postgresql.ENUM(
                    "button", "dropdown", "filter", "checkbox", "toggle", "slider",
                    "three_dot_menu", "tab", "modal", "other",
                    name="sow_ui_element_type_enum", create_type=False,
                ),
                nullable=True,
            ),
            sa.Column("label", sa.String(500), nullable=False),
            sa.Column("location", sa.String(500), nullable=True),
            sa.Column("behavior_notes", sa.Text, nullable=True),
            sa.Column("source_ref", sa.String(500), nullable=True),
            sa.Column("assigned_section_key", sa.String(200), nullable=True),
            # Retired (not deleted) when a patch supersedes it with a fresher
            # fact from a newer source -- keeps the ledger's audit trail
            # intact rather than losing history on every rewrite.
            sa.Column(
                "superseded", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_sow_requirements_ledger_document_id",
            "sow_requirements_ledger", ["document_id"],
        )
        op.create_index(
            "ix_sow_requirements_ledger_source_artifact_id",
            "sow_requirements_ledger", ["source_artifact_id"],
        )

    # ── Table 5: sow_generation_jobs ────────────────────────────────────────
    if not _table_exists("sow_generation_jobs"):
        op.create_table(
            "sow_generation_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "version_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sow_document_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "stage",
                postgresql.ENUM(
                    "ledger_extraction", "drafting", "audit", "assembly",
                    name="sow_job_stage_enum", create_type=False,
                ),
                nullable=False,
                server_default="ledger_extraction",
            ),
            sa.Column("stage_progress", sa.String(200), nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "queued", "running", "done", "done_with_errors", "error",
                    name="sow_job_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("started_at", sa.DateTime, nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
            # Bumped on every stage/progress transition -- what
            # sow_reconcile.py's stale-job sweep keys off (plan §11.2).
            sa.Column(
                "updated_at", sa.DateTime, server_default=sa.func.now(),
                onupdate=sa.func.now(), nullable=False,
            ),
        )
        op.create_index(
            "ix_sow_generation_jobs_document_id", "sow_generation_jobs", ["document_id"]
        )
        op.create_index(
            "ix_sow_generation_jobs_version_id", "sow_generation_jobs", ["version_id"]
        )


def downgrade() -> None:
    # sow_documents.current_version_id FK's -> sow_document_versions (added
    # separately in upgrade() to break the circular dependency at create
    # time -- see Table 1's comment). Postgres refuses to DROP TABLE
    # sow_document_versions while that constraint still references it, so
    # it must be dropped explicitly first, before any table drops.
    op.execute(
        "ALTER TABLE sow_documents "
        "DROP CONSTRAINT IF EXISTS fk_sow_documents_current_version_id"
    )
    # Now strict FK-dependency order (children before parents).
    op.drop_table("sow_generation_jobs")       # -> documents, versions
    op.drop_table("sow_requirements_ledger")   # -> documents
    op.drop_table("sow_sections")              # -> versions
    op.drop_table("sow_document_versions")     # -> documents
    op.drop_table("sow_documents")
    for enum_name in (
        "sow_ui_element_type_enum",
        "sow_ledger_fact_type_enum",
        "sow_job_status_enum",
        "sow_job_stage_enum",
        "sow_section_status_enum",
        "sow_version_status_enum",
        "sow_version_kind_enum",
        "sow_document_status_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    # artifact_type_enum's new values ('meeting_transcript',
    # 'meeting_recording') are intentionally NOT removed on downgrade --
    # Postgres does not support dropping individual enum values, and this
    # matches the existing precedent in this repo (0008/0017 leave added
    # enum values in place on downgrade rather than attempting a rebuild).
