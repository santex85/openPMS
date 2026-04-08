"""Inbound Channex booking revision queue row."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ChannexBookingRevision(Base):
    __tablename__ = "channex_booking_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            ["channex_property_links.tenant_id", "channex_property_links.id"],
            name="fk_channex_booking_revisions_property_link_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "openpms_booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_channex_booking_revisions_booking_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_booking_revisions_tenant_id_id",
        ),
        UniqueConstraint(
            "channex_revision_id",
            name="uq_channex_booking_revisions_channex_revision_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_link_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    channex_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channex_booking_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    channel_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    openpms_booking_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
