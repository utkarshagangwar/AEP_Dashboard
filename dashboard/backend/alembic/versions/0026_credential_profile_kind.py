"""Add credential profile kind + ad-hoc run login fields

Adds the ability to mark a credential profile as a "bypass" kind (injects an
auth cookie obtained via an admin API-key login call, instead of typing
credentials into a login form — used to route around CAPTCHA-gated login
forms the AI agent cannot and should not try to solve) plus per-run ad-hoc
target URL/login fields for one-off runs against environments that have no
saved credential profile at all.

kind is nullable — null/absent means today's only kind (plain username +
password via sensitive_data), matching AISkill.source_type's existing
nullable-discriminator convention rather than a native enum, since this is
currently a 2-value discriminator.

Revision ID: 0026_credential_profile_kind
Revises: 0025_add_video_platform_name
Create Date: 2026-07-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_credential_profile_kind"
down_revision: Union[str, None] = "0025_add_video_platform_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def upgrade() -> None:
    if not _column_exists("ai_credential_profiles", "kind"):
        op.add_column(
            "ai_credential_profiles",
            sa.Column("kind", sa.String(length=20), nullable=True),
        )
    if not _column_exists("ai_credential_profiles", "target_url"):
        op.add_column(
            "ai_credential_profiles",
            sa.Column("target_url", sa.Text(), nullable=True),
        )
    if not _column_exists("ai_test_runs", "adhoc_target_url"):
        op.add_column(
            "ai_test_runs",
            sa.Column("adhoc_target_url", sa.Text(), nullable=True),
        )
    if not _column_exists("ai_test_runs", "adhoc_credentials_json"):
        op.add_column(
            "ai_test_runs",
            sa.Column("adhoc_credentials_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("ai_test_runs", "adhoc_credentials_json"):
        op.drop_column("ai_test_runs", "adhoc_credentials_json")
    if _column_exists("ai_test_runs", "adhoc_target_url"):
        op.drop_column("ai_test_runs", "adhoc_target_url")
    if _column_exists("ai_credential_profiles", "target_url"):
        op.drop_column("ai_credential_profiles", "target_url")
    if _column_exists("ai_credential_profiles", "kind"):
        op.drop_column("ai_credential_profiles", "kind")
