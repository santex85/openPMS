"""Create booking: pricing, ledger lock, guest, booking, lines, folio."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.rates.rate_plan import RatePlan
from app.models.core.room_type import RoomType
from app.schemas.bookings import (
    BookingCreateRequest,
    BookingCreateResponse,
    NightlyPriceLine,
)
from app.services.availability_lock import (
    increment_booked_rooms,
    lock_and_validate_availability,
)
from app.services.pricing_service import sum_rates_for_stay
from app.services.stay_dates import iter_stay_nights


class InvalidBookingContextError(Exception):
    """Room type or rate plan is missing or not under the given property."""


async def _require_room_type_on_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
) -> RoomType:
    stmt = select(RoomType).where(
        RoomType.tenant_id == tenant_id,
        RoomType.id == room_type_id,
        RoomType.property_id == property_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidBookingContextError(
            "room_type not found for this property",
        )
    return row


async def _require_rate_plan_on_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    rate_plan_id: UUID,
) -> RatePlan:
    stmt = select(RatePlan).where(
        RatePlan.tenant_id == tenant_id,
        RatePlan.id == rate_plan_id,
        RatePlan.property_id == property_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidBookingContextError(
            "rate_plan not found for this property",
        )
    return row


async def create_booking(
    session: AsyncSession,
    tenant_id: UUID,
    body: BookingCreateRequest,
) -> BookingCreateResponse:
    nights = iter_stay_nights(body.check_in, body.check_out)

    total, per_night = await sum_rates_for_stay(
        session,
        tenant_id,
        body.room_type_id,
        body.rate_plan_id,
        body.check_in,
        body.check_out,
    )

    await _require_room_type_on_property(
        session,
        tenant_id,
        body.property_id,
        body.room_type_id,
    )
    await _require_rate_plan_on_property(
        session,
        tenant_id,
        body.property_id,
        body.rate_plan_id,
    )

    ledger_rows = await lock_and_validate_availability(
        session,
        tenant_id,
        body.room_type_id,
        nights,
        rooms_to_book=1,
    )
    increment_booked_rooms(ledger_rows, 1)

    guest = Guest(
        tenant_id=tenant_id,
        first_name=body.guest.first_name.strip(),
        last_name=body.guest.last_name.strip(),
        email=body.guest.email.strip(),
        phone=body.guest.phone.strip(),
        passport_data=(
            body.guest.passport_data.strip()
            if body.guest.passport_data
            else None
        ),
    )
    session.add(guest)
    await session.flush()

    booking = Booking(
        tenant_id=tenant_id,
        property_id=body.property_id,
        guest_id=guest.id,
        status=body.status.strip(),
        source=body.source.strip(),
        total_amount=total,
    )
    session.add(booking)
    await session.flush()

    for night, price in per_night:
        session.add(
            BookingLine(
                tenant_id=tenant_id,
                booking_id=booking.id,
                date=night,
                room_type_id=body.room_type_id,
                room_id=None,
                price_for_date=price,
            ),
        )

    session.add(
        FolioTransaction(
            tenant_id=tenant_id,
            booking_id=booking.id,
            transaction_type="Charge",
            amount=total,
            payment_method=None,
        ),
    )
    await session.flush()

    return BookingCreateResponse(
        booking_id=booking.id,
        guest_id=guest.id,
        total_amount=total,
        nights=[NightlyPriceLine(date=d, price=p) for d, p in per_night],
    )
