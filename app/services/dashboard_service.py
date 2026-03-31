"""Property-level operational KPIs for dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.dashboard import DashboardSummaryRead


class DashboardServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


_ACTIVE_EXCLUDE = ("cancelled", "no_show")


async def get_dashboard_summary(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> DashboardSummaryRead:
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise DashboardServiceError("property not found", status_code=404)

    min_date_subq = (
        select(func.min(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    arrivals = int(
        await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.property_id == property_id,
                Booking.status.notin_(_ACTIVE_EXCLUDE),
                min_date_subq == today,
            ),
        )
        or 0,
    )

    max_date_subq = (
        select(func.max(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    departures = int(
        await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.property_id == property_id,
                Booking.status.notin_(_ACTIVE_EXCLUDE),
                max_date_subq == yesterday,
            ),
        )
        or 0,
    )

    ledger_row = await session.execute(
        select(
            func.coalesce(func.sum(AvailabilityLedger.booked_rooms), 0),
            func.coalesce(func.sum(AvailabilityLedger.total_rooms), 0),
        )
        .select_from(AvailabilityLedger)
        .join(
            RoomType,
            (RoomType.tenant_id == AvailabilityLedger.tenant_id)
            & (RoomType.id == AvailabilityLedger.room_type_id),
        )
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            RoomType.property_id == property_id,
            AvailabilityLedger.date == today,
        ),
    )
    occupied_raw, total_raw = ledger_row.one()
    occupied_rooms = int(occupied_raw)
    total_rooms = int(total_raw)

    dirty_rooms = int(
        await session.scalar(
            select(func.count())
            .select_from(Room)
            .join(
                RoomType,
                (RoomType.tenant_id == Room.tenant_id)
                & (RoomType.id == Room.room_type_id),
            )
            .where(
                Room.tenant_id == tenant_id,
                RoomType.property_id == property_id,
                Room.housekeeping_status == "dirty",
                Room.deleted_at.is_(None),
            ),
        )
        or 0,
    )

    return DashboardSummaryRead(
        arrivals_today=arrivals,
        departures_today=departures,
        occupied_rooms=occupied_rooms,
        total_rooms=total_rooms,
        dirty_rooms=dirty_rooms,
        currency=prop.currency,
    )
