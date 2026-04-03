"""Property-level operational KPIs for dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.bookings import BookingUnpaidFolioSummaryRead
from app.schemas.dashboard import DashboardSummaryRead
from app.services.folio_service import list_unpaid_folio_summary_for_property


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
    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise DashboardServiceError("property not found", status_code=404)

    tz_id = prop.timezone.strip()
    if not tz_id:
        raise DashboardServiceError(
            "property timezone is empty",
            status_code=400,
        )
    try:
        tz = ZoneInfo(tz_id)
    except (ZoneInfoNotFoundError, ValueError):
        raise DashboardServiceError(
            f"Invalid IANA timezone for property: {prop.timezone!r}. "
            "Use a valid identifier such as Europe/Berlin or Asia/Bangkok.",
            status_code=400,
        ) from None

    today_local = datetime.now(tz).date()
    yesterday_local = today_local - timedelta(days=1)

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
                min_date_subq == today_local,
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
                max_date_subq == yesterday_local,
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
            AvailabilityLedger.date == today_local,
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

    raw_unpaid = await list_unpaid_folio_summary_for_property(
        session, tenant_id, property_id
    )
    unpaid_folio: list[BookingUnpaidFolioSummaryRead] = []
    for bid, bal, fn, ln in raw_unpaid:
        name = f"{fn} {ln}".strip()
        unpaid_folio.append(
            BookingUnpaidFolioSummaryRead(
                booking_id=bid,
                balance=format(bal, "f"),
                guest_name=name if name else None,
            ),
        )

    return DashboardSummaryRead(
        arrivals_today=arrivals,
        departures_today=departures,
        occupied_rooms=occupied_rooms,
        total_rooms=total_rooms,
        dirty_rooms=dirty_rooms,
        currency=prop.currency,
        unpaid_folio=unpaid_folio,
    )
