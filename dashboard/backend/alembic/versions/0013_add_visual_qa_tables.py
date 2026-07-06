"""Add Visual QA tables: design_artifacts, design_rules, visual_runs, visual_findings

Revision ID: 0013_visual_qa
Revises: 0012_ai_test_runs
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_visual_qa"
down_revision: Union[str, None] = "0012_ai_test_runs"
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


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name}
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # ── Enums (raw SQL so SQLAlchemy never auto-emits CREATE TYPE) ──────────
    if not _enum_exists("artifact_type_enum"):
        op.execute(
            "CREATE TYPE artifact_type_enum AS ENUM ('figma_png', 'sow', 'video')"
        )
    if not _enum_exists("visual_run_status_enum"):
        op.execute(
            "CREATE TYPE visual_run_status_enum AS ENUM "
            "('pending', 'running', 'passed', 'failed', 'partial', 'error', 'cancelled')"
        )
    if not _enum_exists("finding_engine_enum"):
        op.execute("CREATE TYPE finding_engine_enum AS ENUM ('pixel_diff', 'vision')")
    if not _enum_exists("finding_severity_enum"):
        op.execute(
            "CREATE TYPE finding_severity_enum AS ENUM "
            "('critical', 'major', 'minor', 'info')"
        )

    # ── Table 1: design_artifacts ───────────────────────────────────────────
    if not _table_exists("design_artifacts"):
        op.create_table(
            "design_artifacts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "artifact_type",
                postgresql.ENUM(
                    "figma_png", "sow", "video",
                    name="artifact_type_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("file_name", sa.String(500), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("storage_path", sa.Text, nullable=False),
            sa.Column("target_page", sa.String(1000), nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index(
            "ix_design_artifacts_project_id", "design_artifacts", ["project_id"]
        )
        op.create_index("ix_design_artifacts_sha256", "design_artifacts", ["sha256"])

    # ── Table 2: design_rules ───────────────────────────────────────────────
    if not _table_exists("design_rules"):
        op.create_table(
            "design_rules",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("checkpoints", postgresql.JSONB, nullable=False),
            sa.Column("parsed_by_model", sa.String(200), nullable=True),
            sa.Column(
                "parsed_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("ix_design_rules_artifact_id", "design_rules", ["artifact_id"])

    # ── Table 3: visual_runs ────────────────────────────────────────────────
    if not _table_exists("visual_runs"):
        op.create_table(
            "visual_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("environment", sa.String(200), nullable=True),
            sa.Column("target_url", sa.Text, nullable=False),
            sa.Column(
                "artifact_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("design_artifacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending", "running", "passed", "failed",
                    "partial", "error", "cancelled",
                    name="visual_run_status_enum", create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("screenshot_path", sa.Text, nullable=True),
            sa.Column("diff_image_path", sa.Text, nullable=True),
            sa.Column("pixel_mismatch_pct", sa.Integer, nullable=True),
            sa.Column("summary", sa.Text, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("started_at", sa.DateTime, nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("ix_visual_runs_project_id", "visual_runs", ["project_id"])

    # ── Table 4: visual_findings ────────────────────────────────────────────
    if not _table_exists("visual_findings"):
        op.create_table(
            "visual_findings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("visual_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "engine",
                postgresql.ENUM(
                    "pixel_diff", "vision",
                    name="finding_engine_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "severity",
                postgresql.ENUM(
                    "critical", "major", "minor", "info",
                    name="finding_severity_enum", create_type=False,
                ),
                nullable=False,
                server_default="minor",
            ),
            sa.Column("element", sa.String(500), nullable=True),
            sa.Column("issue", sa.Text, nullable=False),
            sa.Column("expected", sa.Text, nullable=True),
            sa.Column("actual", sa.Text, nullable=True),
            sa.Column("region", postgresql.JSONB, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("ix_visual_findings_run_id", "visual_findings", ["run_id"])


def downgrade() -> None:
    op.drop_table("visual_findings")
    op.drop_table("visual_runs")
    op.drop_table("design_rules")
    op.drop_table("design_artifacts")
    for enum_name in (
        "finding_severity_enum",
        "finding_engine_enum",
        "visual_run_status_enum",
        "artifact_type_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
