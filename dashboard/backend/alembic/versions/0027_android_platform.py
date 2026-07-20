"""Add Android platform support to AI test runs

Adds android_app_builds (uploaded APK metadata + cloud device-farm app id)
and a platform column on ai_test_runs distinguishing "web" (unchanged,
default) from "android" runs, plus the columns an Android run needs
(android_app_build_id/_name, device_profile, platform_metadata).

platform is nullable-free (server_default="web") and deliberately orthogonal
to run_type (execution-origin: "ai" vs "skill_replay") — a plain string
discriminator, matching AISkill.source_type's existing convention, not a
native enum, since this is a small still-growing set of values.

Revision ID: 0027_android_platform
Revises: 0026_credential_profile_kind
Create Date: 2026-07-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_android_platform"
down_revision: Union[str, None] = "0026_credential_profile_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    return result.fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # Must exist before ai_test_runs.android_app_build_id below (FK target).
    if not _table_exists("android_app_builds"):
        op.create_table(
            "android_app_builds",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("apk_filename", sa.String(500), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column(
                "farm_vendor", sa.String(20), nullable=False, server_default="browserstack"
            ),
            sa.Column("farm_app_id", sa.Text(), nullable=False),
            sa.Column("package_name", sa.String(300), nullable=True),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_android_app_builds_project_id", "android_app_builds", ["project_id"]
        )
        op.create_index(
            "ix_android_app_builds_sha256", "android_app_builds", ["sha256"]
        )

    if not _column_exists("ai_test_runs", "platform"):
        op.add_column(
            "ai_test_runs",
            sa.Column("platform", sa.String(20), nullable=False, server_default="web"),
        )
    if not _column_exists("ai_test_runs", "android_app_build_id"):
        op.add_column(
            "ai_test_runs",
            sa.Column(
                "android_app_build_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("android_app_builds.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not _column_exists("ai_test_runs", "android_app_build_name"):
        op.add_column(
            "ai_test_runs",
            sa.Column("android_app_build_name", sa.String(300), nullable=True),
        )
    if not _column_exists("ai_test_runs", "device_profile"):
        op.add_column(
            "ai_test_runs", sa.Column("device_profile", sa.String(100), nullable=True)
        )
    if not _column_exists("ai_test_runs", "platform_metadata"):
        op.add_column(
            "ai_test_runs", sa.Column("platform_metadata", postgresql.JSONB(), nullable=True)
        )


def downgrade() -> None:
    # Drop ai_test_runs columns first — android_app_build_id FKs the table
    # dropped below.
    for col in (
        "platform_metadata",
        "device_profile",
        "android_app_build_name",
        "android_app_build_id",
        "platform",
    ):
        if _column_exists("ai_test_runs", col):
            op.drop_column("ai_test_runs", col)
    if _table_exists("android_app_builds"):
        op.drop_table("android_app_builds")
