"""Move the default login from the environment row up to the project

Why:

  Migration 0041 put `default_credential_profile_id` on
  project_environments, which made "which login do I use?" depend on
  "did this run's environment label match a configured row?".

  A prompt skill extracted from a SOW has environment = NULL —
  app.services.skill_store.upsert_prompt_skill never sets it. So any
  lookup miss (no rows saved yet, an unmatched label, or two rows and no
  label to choose by) silently dropped the LOGIN as well as the URL. The
  run then started unauthenticated, the agent walked into the target
  app's real login form, and hit the reCAPTCHA that a kind="bypass"
  profile exists specifically to route around. Observed against
  dev.interviewgod.ai: the run's action log had no "Inject authenticated
  session cookie" step at all, confirming cookies were never resolved.

  A login describes the application under test, not one of its
  addresses. It belongs on the project.

Data migration:

  Any default_credential_profile_id already configured on an environment
  row is lifted to its project, so nothing set up under the 0041 UI has
  to be re-entered. Where a project has several environment rows with
  different profiles, the OLDEST row wins — deterministic, and it
  matches "the first one you configured is the one you meant as the
  default". Such a project is a genuine ambiguity the data cannot
  resolve, so the choice is recorded rather than guessed at silently:
  the per-environment column is left in place (see below) so the
  original values remain inspectable.

  project_environments.default_credential_profile_id is deliberately NOT
  dropped. Keeping it makes this migration reversible without data loss
  and leaves an audit trail of what was configured before the lift.
  Nothing reads it at run time any more — see
  app.services.start_context.

Revision ID: 0042_project_default_login
Revises: 0041_project_environments
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_project_default_login"
down_revision: Union[str, None] = "0041_project_environments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "default_credential_profile_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_projects_default_credential_profile",
        "projects",
        "ai_credential_profiles",
        ["default_credential_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Lift whatever was configured under the 0041 UI. DISTINCT ON picks
    # one row per project deterministically (oldest first) rather than
    # letting an arbitrary row win, so re-running this is idempotent and
    # two operators reviewing the same database see the same outcome.
    op.execute(
        """
        UPDATE projects p
        SET default_credential_profile_id = src.default_credential_profile_id
        FROM (
            SELECT DISTINCT ON (project_id)
                   project_id,
                   default_credential_profile_id
            FROM project_environments
            WHERE default_credential_profile_id IS NOT NULL
            ORDER BY project_id, created_at ASC
        ) AS src
        WHERE p.id = src.project_id
          AND p.default_credential_profile_id IS NULL
        """
    )


def downgrade() -> None:
    # The per-environment column was never dropped, so the pre-0042
    # configuration is still intact and nothing needs restoring here.
    op.drop_constraint(
        "fk_projects_default_credential_profile", "projects", type_="foreignkey"
    )
    op.drop_column("projects", "default_credential_profile_id")
