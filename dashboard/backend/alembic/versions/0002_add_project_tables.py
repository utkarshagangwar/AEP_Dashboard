"""add project tables: projects, test_suites, test_runs, test_results, defects

Revision ID: 0002_add_project_tables
Revises: 0001_initial_auth
Create Date: 2026-06-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_add_project_tables"
down_revision: Union[str, None] = "0001_initial_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


product_enum = postgresql.ENUM(
    "vikaas",
    "vidya",
    "atg_meeting_recorder",
    "axon",
    "revops",
    "lms",
    name="product_enum",
    create_type=False,
)

run_status = postgresql.ENUM(
    "pending",
    "running",
    "passed",
    "failed",
    "cancelled",
    "error",
    name="run_status",
    create_type=False,
)

test_status = postgresql.ENUM(
    "passed",
    "failed",
    "skipped",
    "error",
    name="test_status",
    create_type=False,
)

defect_severity = postgresql.ENUM(
    "critical",
    "high",
    "medium",
    "low",
    name="defect_severity",
    create_type=False,
)

defect_status = postgresql.ENUM(
    "open",
    "in_progress",
    "resolved",
    "closed",
    "wont_fix",
    name="defect_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    product_enum.create(bind, checkfirst=True)
    run_status.create(bind, checkfirst=True)
    test_status.create(bind, checkfirst=True)
    defect_severity.create(bind, checkfirst=True)
    defect_status.create(bind, checkfirst=True)

    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("product", product_enum, nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_projects_name", "projects", ["name"], unique=True)

    op.create_table(
        "test_suites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_test_suites_project_id", "test_suites", ["project_id"], unique=False
    )
    op.create_index(
        "ix_test_suites_created_by", "test_suites", ["created_by"], unique=False
    )

    op.create_table(
        "test_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "test_suite_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_suites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column(
            "triggered_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_test_runs_test_suite_id", "test_runs", ["test_suite_id"], unique=False
    )
    op.create_index(
        "ix_test_runs_triggered_by", "test_runs", ["triggered_by"], unique=False
    )

    op.create_table(
        "test_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_name", sa.String(length=500), nullable=False),
        sa.Column("status", test_status, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_test_results_test_run_id", "test_results", ["test_run_id"], unique=False
    )

    op.create_table(
        "defects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "test_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "severity", defect_severity, nullable=False, server_default="medium"
        ),
        sa.Column("status", defect_status, nullable=False, server_default="open"),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_defects_test_result_id", "defects", ["test_result_id"], unique=False
    )
    op.create_index(
        "ix_defects_assigned_to", "defects", ["assigned_to"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_defects_assigned_to", table_name="defects")
    op.drop_index("ix_defects_test_result_id", table_name="defects")
    op.drop_table("defects")

    op.drop_index("ix_test_results_test_run_id", table_name="test_results")
    op.drop_table("test_results")

    op.drop_index("ix_test_runs_triggered_by", table_name="test_runs")
    op.drop_index("ix_test_runs_test_suite_id", table_name="test_runs")
    op.drop_table("test_runs")

    op.drop_index("ix_test_suites_created_by", table_name="test_suites")
    op.drop_index("ix_test_suites_project_id", table_name="test_suites")
    op.drop_table("test_suites")

    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_table("projects")

    bind = op.get_bind()
    defect_status.drop(bind, checkfirst=True)
    defect_severity.drop(bind, checkfirst=True)
    test_status.drop(bind, checkfirst=True)
    run_status.drop(bind, checkfirst=True)
    product_enum.drop(bind, checkfirst=True)
