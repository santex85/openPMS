"""Channex property connection (per OpenPMS property)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ChannexPropertyLink(Base):
    __tablename__ = "channex_property_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_channex_property_links_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_channex_property_links_property_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_channex_property_links_tenant_id_id"),
        UniqueConstraint("property_id", name="uq_channex_property_links_property_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    channex_property_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channex_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    channex_env: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="production",
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
