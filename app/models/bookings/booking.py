"""Booking header."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.bookings.booking_line import BookingLine
    from app.models.bookings.guest import Guest


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','confirmed','checked_in','checked_out','cancelled','no_show')",
            name="ck_booking_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_bookings_property_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "guest_id"],
            ["guests.tenant_id", "guests.id"],
            name="fk_bookings_guest_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_plan_id"],
            ["rate_plans.tenant_id", "rate_plans.id"],
            name="fk_bookings_rate_plan_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_bookings_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    guest_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    rate_plan_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    external_booking_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    guest: Mapped["Guest"] = relationship(
        "Guest",
        primaryjoin="and_(Booking.tenant_id == Guest.tenant_id, Booking.guest_id == Guest.id)",
        foreign_keys=[tenant_id, guest_id],
        viewonly=True,
    )
    lines: Mapped[list["BookingLine"]] = relationship(back_populates="booking")
