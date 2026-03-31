"""Create booking: pricing, ledger lock, guest, booking, lines, folio."""

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.core.room import Room
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.rates.rate_plan import RatePlan
from app.models.core.room_type import RoomType
from app.schemas.bookings import (
    BookingCreateRequest,
    BookingCreateResponse,
    BookingPatchRequest,
    BookingTapeRead,
    GuestPayload,
    GuestTapeRead,
    NightlyPriceLine,
)
from app.services.availability_lock import (
    decrement_booked_rooms,
    increment_booked_rooms,
    lock_and_validate_availability,
)
from app.services.folio_service import compute_folio_balance
from app.services.pricing_service import MissingRatesError, sum_rates_for_stay
from app.domain.booking_status import (
    BookingStatusTransitionError,
    normalize_booking_status,
    validate_status_transition,
)
from app.services.stay_dates import MAX_STAY_NIGHTS, iter_stay_nights


def _booking_tape_from_mapping(row: Mapping[str, object]) -> BookingTapeRead:
    guest = GuestTapeRead(
        id=row["g_id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
    )
    return BookingTapeRead(
        id=row["id"],
        tenant_id=row["tenant_id"],
        property_id=row["property_id"],
        guest_id=row["guest_id"],
        status=row["status"],
        source=row["source"],
        total_amount=row["total_amount"],
        guest=guest,
        check_in_date=row["check_in_date"],
        check_out_date=row["check_out_date"],
        room_id=row["room_id"],
        room_type_id=row["room_type_id"],
    )


class InvalidBookingContextError(Exception):
    """Room type or rate plan is missing or not under the given property."""


class AssignBookingRoomError(Exception):
    """Cannot assign room to booking (wrong tenant, type, or property)."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class PatchBookingError(Exception):
    """Invalid booking patch (dates, inventory, or room conflict)."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def _get_or_create_guest_for_booking(
    session: AsyncSession,
    tenant_id: UUID,
    payload: GuestPayload,
) -> Guest:
    email_norm = payload.email.strip().lower()
    existing = await session.scalar(
        select(Guest).where(
            Guest.tenant_id == tenant_id,
            Guest.email == email_norm,
        ),
    )
    if existing is not None:
        return existing

    guest = Guest(
        tenant_id=tenant_id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        email=email_norm,
        phone=payload.phone.strip(),
        passport_data=(
            payload.passport_data.strip() if payload.passport_data else None
        ),
    )
    session.add(guest)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        session.expunge(guest)
        again = await session.scalar(
            select(Guest).where(
                Guest.tenant_id == tenant_id,
                Guest.email == email_norm,
            ),
        )
        if again is None:
            raise InvalidBookingContextError(
                "could not create or resolve guest by email",
            ) from None
        return again
    return guest


async def _mark_assigned_rooms_dirty_on_checkout(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> None:
    stmt = select(BookingLine.room_id).where(
        BookingLine.tenant_id == tenant_id,
        BookingLine.booking_id == booking_id,
        BookingLine.room_id.isnot(None),
    )
    result = await session.execute(stmt)
    room_ids = {row[0] for row in result.all()}
    for rid in room_ids:
        room = await session.scalar(
            select(Room).where(
                Room.tenant_id == tenant_id,
                Room.id == rid,
                Room.deleted_at.is_(None),
            ),
        )
        if room is not None:
            room.housekeeping_status = "dirty"


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

    guest = await _get_or_create_guest_for_booking(
        session,
        tenant_id,
        body.guest,
    )

    booking = Booking(
        tenant_id=tenant_id,
        property_id=body.property_id,
        guest_id=guest.id,
        rate_plan_id=body.rate_plan_id,
        status=body.status,
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
            description="Room charge (stay)",
            created_by=None,
            category="room_charge",
        ),
    )
    await session.flush()

    return BookingCreateResponse(
        booking_id=booking.id,
        guest_id=guest.id,
        total_amount=total,
        nights=[NightlyPriceLine(date=d, price=p) for d, p in per_night],
    )


async def list_bookings(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[Booking]:
    stmt = (
        select(Booking).where(Booking.tenant_id == tenant_id).order_by(Booking.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _booking_tape_list_params(
    tenant_id: UUID,
    property_id: UUID,
    start_date: date,
    end_date: date,
    status_filter: str | None,
) -> tuple[str, dict[str, object]]:
    status_clause = ""
    params: dict[str, object] = {
        "tenant_id": str(tenant_id),
        "property_id": str(property_id),
        "start_date": start_date,
        "end_date": end_date,
    }
    if status_filter is not None:
        status_clause = " AND b.status = :status"
        params["status"] = status_filter
    where = (
        """
WHERE b.tenant_id = CAST(:tenant_id AS uuid)
  AND b.property_id = CAST(:property_id AS uuid)
  AND EXISTS (
    SELECT 1 FROM booking_lines bl
    WHERE bl.booking_id = b.id AND bl.tenant_id = b.tenant_id
      AND bl.date >= :start_date AND bl.date <= :end_date
  )
"""
        + status_clause
    )
    return where, params


async def list_bookings_enriched(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID,
    start_date: date,
    end_date: date,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[BookingTapeRead], int]:
    where, base_params = _booking_tape_list_params(
        tenant_id,
        property_id,
        start_date,
        end_date,
        status_filter,
    )
    count_sql = text(
        "SELECT count(*)::int AS n FROM bookings b\n" + where,
    )
    total = int((await session.execute(count_sql, base_params)).scalar_one())

    data_params = dict(base_params)
    data_params["limit"] = limit
    data_params["offset"] = offset
    sql = text(
        _LINE_AGG_CTE_IN_WINDOW
        + _BOOKING_TAPE_SELECT
        + where
        + """
ORDER BY b.id
LIMIT :limit OFFSET :offset
""",
    )
    result = await session.execute(sql, data_params)
    rows = [_booking_tape_from_mapping(row) for row in result.mappings().all()]
    return rows, total


_LINE_AGG_CTE_IN_WINDOW = """
WITH touch AS (
  SELECT DISTINCT tenant_id, booking_id
  FROM booking_lines
  WHERE tenant_id = CAST(:tenant_id AS uuid)
    AND date >= :start_date AND date <= :end_date
),
line_agg AS (
  SELECT
    bl.tenant_id,
    bl.booking_id,
    MIN(bl.date) AS check_in_date,
    (MAX(bl.date) + INTERVAL '1 day')::date AS check_out_date,
    COUNT(DISTINCT bl.room_type_id) AS rt_cnt,
    MIN(bl.room_type_id::text)::uuid AS rt_min,
    COUNT(DISTINCT bl.room_id) FILTER (WHERE bl.room_id IS NOT NULL) AS rm_cnt,
    (MAX(bl.room_id::text) FILTER (WHERE bl.room_id IS NOT NULL))::uuid AS rm_val
  FROM booking_lines bl
  INNER JOIN touch t
    ON t.tenant_id = bl.tenant_id AND t.booking_id = bl.booking_id
  GROUP BY bl.tenant_id, bl.booking_id
)
"""

_LINE_AGG_CTE = """
WITH line_agg AS (
  SELECT
    tenant_id,
    booking_id,
    MIN(date) AS check_in_date,
    (MAX(date) + INTERVAL '1 day')::date AS check_out_date,
    COUNT(DISTINCT room_type_id) AS rt_cnt,
    MIN(room_type_id::text)::uuid AS rt_min,
    COUNT(DISTINCT room_id) FILTER (WHERE room_id IS NOT NULL) AS rm_cnt,
    (MAX(room_id::text) FILTER (WHERE room_id IS NOT NULL))::uuid AS rm_val
  FROM booking_lines
  GROUP BY tenant_id, booking_id
)
"""

_BOOKING_TAPE_SELECT = """
SELECT
  b.id,
  b.tenant_id,
  b.property_id,
  b.guest_id,
  b.status,
  b.source,
  b.total_amount,
  g.id AS g_id,
  g.first_name,
  g.last_name,
  la.check_in_date,
  la.check_out_date,
  CASE WHEN la.rt_cnt = 1 THEN la.rt_min ELSE NULL END AS room_type_id,
  CASE WHEN la.rm_cnt = 1 THEN la.rm_val ELSE NULL END AS room_id
FROM bookings b
JOIN line_agg la ON la.booking_id = b.id AND la.tenant_id = b.tenant_id
JOIN guests g ON g.tenant_id = b.tenant_id AND g.id = b.guest_id
"""


async def get_booking_tape(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> BookingTapeRead | None:
    """Single booking tape row by id (any stay dates); None if missing or no lines."""
    sql = text(
        _LINE_AGG_CTE
        + _BOOKING_TAPE_SELECT
        + """
WHERE b.tenant_id = CAST(:tenant_id AS uuid)
  AND b.id = CAST(:booking_id AS uuid)
""",
    )
    result = await session.execute(
        sql,
        {"tenant_id": str(tenant_id), "booking_id": str(booking_id)},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return _booking_tape_from_mapping(row)


def _stay_bounds_from_lines(lines: list[BookingLine]) -> tuple[date, date]:
    nights = [ln.date for ln in lines]
    return min(nights), max(nights) + timedelta(days=1)


async def _assert_no_room_conflict(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
    nights: list[date],
    exclude_booking_id: UUID,
) -> None:
    if not nights:
        return
    stmt = (
        select(BookingLine.id)
        .join(
            Booking,
            (Booking.tenant_id == BookingLine.tenant_id)
            & (Booking.id == BookingLine.booking_id),
        )
        .where(
            BookingLine.tenant_id == tenant_id,
            BookingLine.room_id == room_id,
            BookingLine.date.in_(nights),
            BookingLine.booking_id != exclude_booking_id,
            Booking.status.notin_(["cancelled", "no_show"]),
        )
        .limit(1)
    )
    clash = await session.scalar(stmt)
    if clash is not None:
        raise AssignBookingRoomError(
            "room is already used by another active booking on overlapping nights",
            status_code=409,
        )


async def _release_booking_inventory(
    session: AsyncSession,
    tenant_id: UUID,
    booking: Booking,
) -> None:
    lines = list(booking.lines)
    if not lines:
        return
    rt_ids = {ln.room_type_id for ln in lines}
    if len(rt_ids) != 1:
        return
    room_type_id = next(iter(rt_ids))
    nights = [ln.date for ln in lines]
    rows = await lock_and_validate_availability(
        session,
        tenant_id,
        room_type_id,
        nights,
        rooms_to_book=0,
    )
    decrement_booked_rooms(rows, 1)


async def _update_folio_charge_amount(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    amount: Decimal,
) -> None:
    stmt = (
        select(FolioTransaction)
        .where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
            FolioTransaction.transaction_type == "Charge",
            FolioTransaction.category == "room_charge",
        )
        .order_by(FolioTransaction.created_at.asc())
        .limit(1)
    )
    ft = await session.scalar(stmt)
    if ft is not None:
        ft.amount = amount


async def assign_booking_room(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    room_id: UUID | None,
) -> None:
    booking = await session.scalar(
        select(Booking)
        .where(
            Booking.tenant_id == tenant_id,
            Booking.id == booking_id,
        )
        .options(selectinload(Booking.lines)),
    )
    if booking is None:
        raise AssignBookingRoomError("booking not found", status_code=404)

    if booking.status.strip().lower() in ("cancelled", "no_show"):
        raise AssignBookingRoomError(
            "cannot assign room on an inactive booking",
            status_code=409,
        )

    lines = list(booking.lines)
    if not lines:
        raise AssignBookingRoomError("booking has no lines")

    rt_ids = {ln.room_type_id for ln in lines}
    if len(rt_ids) != 1:
        raise AssignBookingRoomError(
            "booking lines must share a single room type",
            status_code=409,
        )
    line_room_type_id = next(iter(rt_ids))

    nights = sorted({ln.date for ln in lines})

    if room_id is None:
        for ln in lines:
            ln.room_id = None
        return

    room = await session.scalar(
        select(Room).where(
            Room.tenant_id == tenant_id,
            Room.id == room_id,
            Room.deleted_at.is_(None),
        ),
    )
    if room is None:
        raise AssignBookingRoomError("room not found", status_code=404)

    if room.room_type_id != line_room_type_id:
        raise AssignBookingRoomError(
            "room category does not match booking",
            status_code=409,
        )

    rt = await session.scalar(
        select(RoomType).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == room.room_type_id,
        ),
    )
    if rt is None or rt.property_id != booking.property_id:
        raise AssignBookingRoomError(
            "room is not on the same property as the booking",
            status_code=409,
        )

    await _assert_no_room_conflict(
        session,
        tenant_id,
        room_id,
        nights,
        booking.id,
    )

    for ln in lines:
        ln.room_id = room_id


async def patch_booking(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    body: BookingPatchRequest,
) -> Decimal | None:
    """
    Returns non-zero folio balance when status transitions to checked_out (checkout warning).
    """
    data = body.model_dump(exclude_unset=True)
    if not data:
        return None

    booking = await session.scalar(
        select(Booking)
        .where(
            Booking.tenant_id == tenant_id,
            Booking.id == booking_id,
        )
        .options(selectinload(Booking.lines)),
    )
    if booking is None:
        raise PatchBookingError("booking not found", status_code=404)

    prev_booking_status = booking.status
    lines = list(booking.lines)

    status_in = data.get("status")
    if (
        status_in is not None
        and normalize_booking_status(str(status_in)) == "cancelled"
    ):
        if normalize_booking_status(booking.status) != "cancelled":
            try:
                validate_status_transition(booking.status, "cancelled")
            except BookingStatusTransitionError as exc:
                raise PatchBookingError(exc.message, status_code=409) from exc
            await _release_booking_inventory(session, tenant_id, booking)
            booking.status = "cancelled"
            await session.flush()
        return None

    inactive = normalize_booking_status(booking.status) in ("cancelled", "no_show")
    if inactive:
        if "status" in data:
            try:
                validate_status_transition(booking.status, str(data["status"]))
            except BookingStatusTransitionError as exc:
                raise PatchBookingError(exc.message, status_code=409) from exc
        if {"check_in", "check_out", "room_id"} & data.keys():
            raise PatchBookingError(
                "cannot change stay dates or room on an inactive booking",
                status_code=409,
            )

    if "check_in" in data or "check_out" in data:
        if not lines:
            raise PatchBookingError("booking has no lines", status_code=409)
        ci, co = _stay_bounds_from_lines(lines)
        new_ci = data.get("check_in", ci)
        new_co = data.get("check_out", co)
        if new_co <= new_ci:
            raise PatchBookingError("check_out must be after check_in", status_code=422)
        if (new_co - new_ci).days > MAX_STAY_NIGHTS:
            raise PatchBookingError(
                f"stay cannot exceed {MAX_STAY_NIGHTS} nights",
                status_code=422,
            )

        if new_ci != ci or new_co != co:
            if booking.rate_plan_id is None:
                raise PatchBookingError(
                    "booking has no stored rate_plan_id; cannot repricing on date change",
                    status_code=422,
                )
            rt_ids = {ln.room_type_id for ln in lines}
            if len(rt_ids) != 1:
                raise PatchBookingError(
                    "booking lines must share a single room type",
                    status_code=409,
                )
            room_type_id = next(iter(rt_ids))
            old_nights = sorted({ln.date for ln in lines})
            old_rows = await lock_and_validate_availability(
                session,
                tenant_id,
                room_type_id,
                old_nights,
                rooms_to_book=0,
            )
            decrement_booked_rooms(old_rows, 1)

            try:
                total, per_night = await sum_rates_for_stay(
                    session,
                    tenant_id,
                    room_type_id,
                    booking.rate_plan_id,
                    new_ci,
                    new_co,
                )
            except MissingRatesError as exc:
                raise PatchBookingError(
                    f"missing rates for dates: {[d.isoformat() for d in exc.missing_dates]}",
                    status_code=422,
                ) from exc

            new_nights = list(iter_stay_nights(new_ci, new_co))
            new_rows = await lock_and_validate_availability(
                session,
                tenant_id,
                room_type_id,
                new_nights,
                rooms_to_book=1,
            )
            increment_booked_rooms(new_rows, 1)

            await session.execute(
                delete(BookingLine).where(
                    BookingLine.tenant_id == tenant_id,
                    BookingLine.booking_id == booking.id,
                ),
            )
            await session.flush()

            for night, price in per_night:
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking.id,
                        date=night,
                        room_type_id=room_type_id,
                        room_id=None,
                        price_for_date=price,
                    ),
                )
            booking.total_amount = total
            await _update_folio_charge_amount(
                session,
                tenant_id,
                booking.id,
                total,
            )
            await session.flush()

    if "room_id" in data:
        await assign_booking_room(
            session,
            tenant_id,
            booking_id,
            data["room_id"],
        )

    if "status" in data:
        new_s = normalize_booking_status(str(data["status"]))
        try:
            validate_status_transition(booking.status, new_s)
        except BookingStatusTransitionError as exc:
            raise PatchBookingError(exc.message, status_code=409) from exc
        if (
            new_s == "no_show"
            and normalize_booking_status(prev_booking_status) == "confirmed"
        ):
            await _release_booking_inventory(session, tenant_id, booking)
        booking.status = new_s

    checkout_balance_warning: Decimal | None = None
    if (
        normalize_booking_status(booking.status) == "checked_out"
        and normalize_booking_status(prev_booking_status) != "checked_out"
    ):
        await _mark_assigned_rooms_dirty_on_checkout(
            session,
            tenant_id,
            booking.id,
        )
        bal = await compute_folio_balance(session, tenant_id, booking.id)
        if bal != Decimal("0.00"):
            checkout_balance_warning = bal
    return checkout_balance_warning
