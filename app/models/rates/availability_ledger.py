"""Per-date availability bucket per room type."""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKeyConstraint, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AvailabilityLedger(Base):
    __tablename__ = "availability_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_availability_ledger_room_type_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "room_type_id",
            "date",
            name="uq_availability_ledger_tenant_room_type_date",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    room_type_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    total_rooms: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_rooms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_rooms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
