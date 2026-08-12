"""Project Intelligence — pi_flows.

Deliberately a SEPARATE revision from 0049_project_intelligence_foundation:
this is the one table that changes existing runtime behaviour the moment a
row in it is verified (see app.services.flow_validation.get_flow_model,
replaced in this same change to read from this table). Splitting it out
means 0049 can be applied, rolled back, and re-applied entirely on its own
with zero effect on the SOW extraction pipeline, and the seam-affecting
change is isolated to a single, easy-to-identify revision.

pi_flows stores exactly the object shape flow_validation.build_index() and
render_flow_reference() already parse -- see AEP_Project_Intelligence_
Consolidated_Spec (v3.0) §17.1. Storing anything else would require
changing flow_validation.py, which this feature deliberately never does.

Revision ID: 0050_project_intelligence_flows
Revises: 0049_project_intelligence_foundation
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_pi_flows"
down_revision: Union[str, None] = "0049_pi_foundation"
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
    # pi_status_enum was created in 0049 and stays shared across every
    # Project Intelligence table -- create_type=False so this migration
    # never tries to redefine it.
    pi_status = postgresql.ENUM(
        "pending", "verified", "rejected", "superseded",
        name="pi_status_enum", create_type=False,
    )

    if not _table_exists("pi_flows"):
        op.create_table(
            "pi_flows",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                       server_default=sa.text("gen_random_uuid()")),
            sa.Column("project_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("environment_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("project_environments.id", ondelete="SET NULL"),
                       nullable=True),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            # Exactly the shape flow_validation.build_index() parses:
            # {"entry_state": str|None, "states": [{"id","requires",
            # "name","pages","locked_behaviours"}, ...]}. locked_behaviours
            # is never machine-proposed (spec §17.2 / Open Question 8) --
            # that rule is enforced in services/pi_flow.py, not here.
            sa.Column("model_json", postgresql.JSONB, nullable=False),
            sa.Column("status", pi_status, nullable=False, server_default="pending"),
            # Populated only for machine-proposed versions -- lets the UI
            # show "proposed from N runs" (spec §17.2). Null for a model a
            # human authored from scratch.
            sa.Column("generated_from_run_ids", postgresql.JSONB, nullable=True),
            sa.Column("edited_by", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True),
                       sa.ForeignKey("pi_flows.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                       onupdate=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pi_flows_project_id", "pi_flows", ["project_id"])
        # Exactly one VERIFIED flow model per (project, environment) --
        # this is what makes serving get_flow_model a single unambiguous
        # query rather than "which of several verified rows wins". Pending
        # proposals and superseded/rejected history are unrestricted.
        op.create_index(
            "ux_pi_flows_verified", "pi_flows",
            ["project_id", "environment_id"],
            unique=True,
            postgresql_where=sa.text("status = 'verified'"),
        )


def downgrade() -> None:
    if _table_exists("pi_flows"):
        op.drop_table("pi_flows")
