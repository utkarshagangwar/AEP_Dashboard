"""TDD extraction: classification fields on ai_skills, audit trail on sow_parts.

Supports app.services.tdd_extraction (see TDD_EXTRACTION_SPEC.md).

ai_skills gains the classification a checkpoint now carries:
  test_type      positive | negative | edge
  category       a behaviour class from tdd_extraction.CATEGORIES
  grounding      stated | derived
  behaviour_key  slug shared by every variant of one behaviour
  priority       smoke | sanity | regression

sow_parts gains the testability-gate audit trail:
  excluded_zones  what Stage 0 removed and why
  coverage_json   the per-part coverage scorecard

ALL COLUMNS ARE NULLABLE AND UNBACKFILLED, deliberately. There is no honest
way to infer, after the fact, whether an existing skill was a positive or a
negative case, or which behaviour class its requirement belonged to — the
information was never captured. Writing a guess (e.g. "everything existing is
positive") into the database would be indistinguishable from a real
classification later on. Instead readers treat NULL as "unclassified, assume
the conservative reading", and re-analysing an artifact is the migration
path: it reproduces the skills through the v2 extractor with real values.

Both indexes are plain (non-unique) and on low-cardinality columns; they
exist because the Skills tab filters on them, not for uniqueness.

Revision ID: 0043_tdd_extraction_fields
Revises: 0042_project_default_login
Create Date: 2026-08-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_tdd_extraction_fields"
down_revision: Union[str, None] = "0042_project_default_login"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_skills", sa.Column("test_type", sa.String(length=20), nullable=True))
    op.add_column("ai_skills", sa.Column("category", sa.String(length=50), nullable=True))
    op.add_column("ai_skills", sa.Column("grounding", sa.String(length=20), nullable=True))
    op.add_column("ai_skills", sa.Column("behaviour_key", sa.String(length=120), nullable=True))
    op.add_column("ai_skills", sa.Column("priority", sa.String(length=20), nullable=True))

    op.create_index("ix_ai_skills_test_type", "ai_skills", ["test_type"])
    op.create_index("ix_ai_skills_category", "ai_skills", ["category"])
    op.create_index("ix_ai_skills_behaviour_key", "ai_skills", ["behaviour_key"])

    op.add_column(
        "sow_parts", sa.Column("excluded_zones", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "sow_parts", sa.Column("coverage_json", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sow_parts", "coverage_json")
    op.drop_column("sow_parts", "excluded_zones")

    op.drop_index("ix_ai_skills_behaviour_key", table_name="ai_skills")
    op.drop_index("ix_ai_skills_category", table_name="ai_skills")
    op.drop_index("ix_ai_skills_test_type", table_name="ai_skills")

    op.drop_column("ai_skills", "priority")
    op.drop_column("ai_skills", "behaviour_key")
    op.drop_column("ai_skills", "grounding")
    op.drop_column("ai_skills", "category")
    op.drop_column("ai_skills", "test_type")
