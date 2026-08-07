"""Project environments: map a project+environment label to a real address

Root cause this migration exists to close:

  `projects.environments` is an ARRAY of bare labels ("dev", "staging",
  "production") with nothing behind them, and `ai_credential_profiles.
  target_url` was documented and implemented as meaningful ONLY for
  kind="bypass" profiles. Between the two there was no way to answer the
  question "what URL does a run for this project start at?".

  Consequently a prompt skill extracted from a SOW (app.services.
  skill_store.upsert_prompt_skill) — which is saved *under a project* but
  is never given a credential profile, because parse time knows nothing
  about credentials — resolved to environment_url="about:blank" in
  app.workers.tasks.ai_execution._resolve_run_inputs. ai_runner's
  `if environment_url != "about:blank"` guard then skipped page.goto()
  altogether, so the agent opened a blank tab and reported a blank page
  before a single functional step ran.

Purely additive: one new table, no changes to any existing column, so a
deploy with runs in flight changes no current behaviour. Every existing
path that already resolves a URL (kind="bypass" profiles, ad-hoc
target_url runs, goals with an embedded URL) continues to resolve it the
same way and never consults this table — it is a FALLBACK consulted only
when those produce nothing.

`default_credential_profile_id` is ON DELETE SET NULL rather than CASCADE
on purpose: deleting a credential profile must degrade an environment to
"configured but no default login" (a clear, fixable error at submit time),
never silently delete the environment's address along with it.

Revision ID: 0041_project_environments
Revises: 0040_sow_needs_review
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_project_environments"
down_revision: Union[str, None] = "0040_sow_needs_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_environments",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column(
            "default_credential_profile_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # One address per (project, environment) — the upsert in
        # app/api/v1/projects.py keys off exactly this pair.
        sa.UniqueConstraint(
            "project_id", "environment", name="uq_project_environments_project_env"
        ),
    )
    # Every lookup is "give me this project's environments" (the resolver
    # and the settings UI), so project_id alone is the index that matters;
    # the unique constraint above already covers the (project, env) probe.
    op.create_index(
        "ix_project_environments_project_id",
        "project_environments",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_environments_project_id", table_name="project_environments"
    )
    op.drop_table("project_environments")
