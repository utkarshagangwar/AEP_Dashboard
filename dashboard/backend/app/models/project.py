"""Project ORM model — represents a product under test."""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Product(str, enum.Enum):
    """Products under test."""

    vikaas = "vikaas"
    vidya = "vidya"
    atg_meeting_recorder = "atg_meeting_recorder"
    axon = "axon"
    revops = "revops"
    lms = "lms"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index(
            "ix_projects_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    # Immutable automation-folder key used by suite discovery to re-identify this
    # project on rescans. Never updated by the rename UI — `name` is cosmetic only.
    folder_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    environments: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String),
        nullable=True,
        default=lambda: ["dev", "staging", "production"],
    )
    product: Mapped[Optional[Product]] = mapped_column(
        SAEnum(Product, name="product_enum", native_enum=True),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # How tests for this project sign in, when the run doesn't name a
    # login itself — which is every skill extracted from a SOW, since
    # parse time knows nothing about credentials.
    #
    # This lives on the PROJECT, not on ProjectEnvironment, and that
    # placement is the whole point (migration 0042). It was originally
    # on the environment row, which coupled "which login" to "did the
    # environment label match". A SOW skill has environment=NULL
    # (skill_store.upsert_prompt_skill never sets it), so any lookup
    # miss silently dropped the login too — the run then proceeded
    # unauthenticated, walked into the app's real login form, and hit
    # the CAPTCHA the bypass profile exists precisely to avoid.
    # A login is a property of the application under test, not of one
    # of its addresses, so it belongs here.
    default_credential_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        # SET NULL, not CASCADE — deleting a credential profile must
        # leave the project intact and merely unauthenticated, which
        # surfaces as a clear message, not a vanished project.
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Project id={self.id} name={self.name} product={self.product}>"


class ProjectEnvironment(Base):
    """Where a project actually lives, per environment label.

    `Project.environments` is an ARRAY of bare labels ("dev", "staging",
    "production") — names with nothing behind them. Nothing in the system
    could turn a label into a reachable address, which is precisely why a
    SOW-derived AISkill saved "under a project" had no way to reach that
    project: the run resolved no URL, browser-use was handed
    environment_url="about:blank", ai_runner's `!= "about:blank"` guard
    skipped page.goto() entirely, and the agent reported a blank page
    before any functional step could run.

    This table is the missing mapping. One row per (project, environment):

      base_url
        Where a run for this project+environment starts. For a
        login-gated app this should be the address a logged-in user lands
        on (e.g. .../dashboard), NOT the marketing homepage — same
        reasoning already documented on AICredentialProfile.target_url,
        since a homepage typically renders identical nav regardless of
        auth state and gives the agent a false "I'm logged in" signal.

      default_credential_profile_id
        DEPRECATED as of migration 0042 — retained only so the 0042 data
        migration can read it, and so a downgrade is lossless. The
        project-wide default now lives on Project.
        default_credential_profile_id; see the comment there for why
        coupling the login to an environment-label match was wrong.
        Nothing reads this column at run time any more.

    Resolution is deliberately done at RUN time from this table rather
    than stamped onto each AISkill row at creation time: a project that
    moves environments or rotates a credential profile then fixes every
    existing skill at once, instead of leaving already-saved skills
    pointing at a dead address.
    """

    __tablename__ = "project_environments"
    __table_args__ = (
        # One address per (project, environment). Upserts key off this.
        UniqueConstraint(
            "project_id", "environment", name="uq_project_environments_project_env"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # CASCADE: an environment address is meaningless without its
        # project, unlike a credential profile (SET NULL) which is a
        # reusable, independently-managed object.
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Matches one entry of Project.environments. Free-text rather than an
    # enum for the same reason AISkill.source_type is — projects define
    # their own environment vocabularies and it is still growing.
    environment: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_credential_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        # SET NULL, not CASCADE — deleting a credential profile must not
        # delete the environment's address with it. The environment stays,
        # simply without a default login, and the failure surfaces as a
        # clear "no credential profile" message rather than a vanished row.
        ForeignKey("ai_credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<ProjectEnvironment project={self.project_id} "
            f"env={self.environment} base_url={self.base_url}>"
        )
