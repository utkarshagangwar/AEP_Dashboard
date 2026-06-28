"""Project ORM model — represents a product under test."""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Index, String, Text, func, text
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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product: Mapped[Optional[Product]] = mapped_column(
        SAEnum(Product, name="product_enum", native_enum=True),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
