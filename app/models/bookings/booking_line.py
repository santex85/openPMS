"""Per-night (or per-date) booking line."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKeyConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.bookings.booking import Booking


class BookingLine(Base):
    __tablename__ = "booking_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_booking_lines_booking_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_booking_lines_room_type_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "room_id"],
            ["rooms.tenant_id", "rooms.id"],
            name="fk_booking_lines_room_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_booking_lines_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    booking_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    room_type_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    price_for_date: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    booking: Mapped["Booking"] = relationship(back_populates="lines")
