"""AI Usage tracking — ai_usage_events + ai_key_limits

New admin page (AI Usage, next to Audit Logs) needs somewhere to read
from: every LLM call the platform makes (Hands/Judge/Brain/orchestrator)
gets logged here, plus an optional admin-set manual quota override per
key for providers/keys with no reliable auto-detected limit. See
app/models/ai_usage.py and app/services/ai_usage.py for the full design
rationale (why source/run_type/run_id are free-text like AuditLog rather
than enums/FKs, why Google has no hardcoded RPD constant, etc).

Purely additive — no existing table or model touched.

Revision ID: 0031_ai_usage
Revises: 0030_sow_import
Create Date: 2026-07-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_ai_usage"
down_revision: Union[str, None] = "0030_sow_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def upgrade() -> None:
    if not _table_exists("ai_usage_events"):
        op.create_table(
            "ai_usage_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("provider", sa.String(50), nullable=False),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("key_label", sa.String(120), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("http_status", sa.Integer, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("prompt_tokens", sa.Integer, nullable=True),
            sa.Column("completion_tokens", sa.Integer, nullable=True),
            sa.Column("total_tokens", sa.Integer, nullable=True),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column("attempts", sa.Integer, nullable=True),
            sa.Column("run_type", sa.String(50), nullable=True),
            sa.Column("run_id", sa.String(255), nullable=True),
            sa.Column(
                "created_by", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("extra", postgresql.JSONB, nullable=True),
        )
        op.create_index("ix_ai_usage_events_created_at", "ai_usage_events", ["created_at"])
        op.create_index("ix_ai_usage_events_source", "ai_usage_events", ["source"])
        op.create_index("ix_ai_usage_events_provider", "ai_usage_events", ["provider"])
        op.create_index("ix_ai_usage_events_key_label", "ai_usage_events", ["key_label"])
        op.create_index("ix_ai_usage_events_run_type", "ai_usage_events", ["run_type"])
        op.create_index("ix_ai_usage_events_run_id", "ai_usage_events", ["run_id"])
        # Powers the per-key "usage today" quota calc (WHERE key_label = ...
        # AND created_at >= today) without a full table scan.
        op.create_index(
            "ix_ai_usage_events_key_created",
            "ai_usage_events",
            ["key_label", "created_at"],
        )

    if not _table_exists("ai_key_limits"):
        op.create_table(
            "ai_key_limits",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("key_label", sa.String(120), nullable=False, unique=True),
            sa.Column("provider", sa.String(50), nullable=False),
            sa.Column("limit_type", sa.String(20), nullable=False),
            sa.Column("limit_value", sa.Numeric(14, 4), nullable=False),
            sa.Column("note", sa.Text, nullable=True),
            sa.Column(
                "updated_by", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
            ),
        )
        op.create_index("ix_ai_key_limits_key_label", "ai_key_limits", ["key_label"], unique=True)


def downgrade() -> None:
    op.drop_table("ai_key_limits")
    op.drop_table("ai_usage_events")
