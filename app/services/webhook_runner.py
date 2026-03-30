"""Build webhook payloads and dispatch (call from FastAPI BackgroundTasks)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core import webhook_events as ev
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.rates.availability_ledger import AvailabilityLedger
from app.services.webhook_delivery_engine import dispatch_webhook_event


async def _set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )


def _stay_bounds(lines: list[BookingLine]) -> tuple[date | None, date | None]:
    if not lines:
        return None, None
    nights = [ln.date for ln in lines]
    return min(nights), max(nights) + timedelta(days=1)


def _room_id_from_lines(lines: list[BookingLine]) -> UUID | None:
    for ln in lines:
        if ln.room_id is not None:
            return ln.room_id
    return None


def booking_entity_to_read_dict(booking: Booking) -> dict[str, object]:
    return {
        "id": str(booking.id),
        "tenant_id": str(booking.tenant_id),
        "property_id": str(booking.property_id),
        "guest_id": str(booking.guest_id),
        "rate_plan_id": str(booking.rate_plan_id) if booking.rate_plan_id else None,
        "status": booking.status,
        "source": booking.source,
        "total_amount": format(booking.total_amount, "f"),
    }


def booking_quick_snapshot(booking: Booking) -> dict[str, object | None]:
    lines = list(booking.lines)
    ci, co = _stay_bounds(lines)
    rid = _room_id_from_lines(lines)
    return {
        "guest_id": str(booking.guest_id),
        "status": booking.status.strip().lower(),
        "total_amount": format(booking.total_amount, "f"),
        "check_in": ci.isoformat() if ci else None,
        "check_out": co.isoformat() if co else None,
        "room_id": str(rid) if rid else None,
    }


async def load_booking_for_webhook(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Booking | None:
    return await session.scalar(
        select(Booking)
        .where(
            Booking.tenant_id == tenant_id,
            Booking.id == booking_id,
        )
        .options(selectinload(Booking.lines)),
    )


async def _available_rooms(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    d: date,
) -> int:
    row = await session.scalar(
        select(AvailabilityLedger).where(
            AvailabilityLedger.tenant_id == tenant_id,
            AvailabilityLedger.room_type_id == room_type_id,
            AvailabilityLedger.date == d,
        ),
    )
    if row is None:
        return 0
    return row.total_rooms - row.booked_rooms - row.blocked_rooms


async def emit_availability_for_dates(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    room_type_id: UUID,
    dates: list[date],
) -> None:
    for d in sorted(set(dates)):
        async with factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                avail = await _available_rooms(session, tenant_id, room_type_id, d)
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.AVAILABILITY_CHANGED,
            {
                "room_type_id": str(room_type_id),
                "date": d.isoformat(),
                "available_rooms": avail,
            },
        )


async def run_booking_created_webhook(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_id: UUID,
) -> None:
    payload: dict[str, object] | None = None
    rt_id: UUID | None = None
    nights: list[date] = []
    async with factory() as session:
        async with session.begin():
            await _set_tenant(session, tenant_id)
            booking = await load_booking_for_webhook(session, tenant_id, booking_id)
            if booking is None:
                return
            payload = booking_entity_to_read_dict(booking)
            lines = list(booking.lines)
            rts = {ln.room_type_id for ln in lines}
            if len(rts) == 1:
                rt_id = next(iter(rts))
                nights = [ln.date for ln in lines]
    if payload:
        await dispatch_webhook_event(factory, tenant_id, ev.BOOKING_CREATED, payload)
    if rt_id and nights:
        await emit_availability_for_dates(factory, tenant_id, rt_id, nights)


async def run_rate_updated_webhooks(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    updates: list[tuple[UUID, UUID, date, str]],
) -> None:
    for room_type_id, rate_plan_id, d, price_s in updates:
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.RATE_UPDATED,
            {
                "room_type_id": str(room_type_id),
                "rate_plan_id": str(rate_plan_id),
                "date": d.isoformat(),
                "price": price_s,
            },
        )


async def run_availability_after_override(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    room_type_id: UUID,
    dates: list[date],
) -> None:
    await emit_availability_for_dates(factory, tenant_id, room_type_id, dates)


async def run_booking_patch_webhooks(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_id: UUID,
    *,
    before: dict[str, object | None],
    after: dict[str, object | None],
    cancellation_reason: str | None,
    folio_balance_on_checkout: str | None,
) -> None:
    bs = str(before.get("status") or "").strip().lower()
    a_s = str(after.get("status") or "").strip().lower()

    if a_s == "cancelled" and bs != "cancelled":
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.BOOKING_CANCELLED,
            {
                "booking_id": str(booking_id),
                "cancellation_reason": cancellation_reason or "",
            },
        )

    if a_s == "checked_in" and bs != "checked_in":
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.GUEST_CHECKED_IN,
            {
                "booking_id": str(booking_id),
                "guest_id": str(after.get("guest_id", "")),
                "room_id": str(after.get("room_id") or ""),
            },
        )

    if a_s == "checked_out" and bs != "checked_out":
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.GUEST_CHECKED_OUT,
            {
                "booking_id": str(booking_id),
                "guest_id": str(after.get("guest_id", "")),
                "room_id": str(after.get("room_id") or ""),
                "folio_balance": folio_balance_on_checkout or "0.00",
            },
        )

    changed: dict[str, object | None] = {}
    previous_values: dict[str, object | None] = {}
    for k in ("status", "total_amount", "check_in", "check_out", "room_id"):
        b_v = before.get(k)
        a_v = after.get(k)
        if b_v != a_v:
            changed[k] = a_v
            previous_values[k] = b_v

    if a_s == "cancelled" and bs != "cancelled":
        changed.pop("status", None)
        previous_values.pop("status", None)
    if a_s == "checked_in" and bs != "checked_in":
        changed.pop("status", None)
        previous_values.pop("status", None)
    if a_s == "checked_out" and bs != "checked_out":
        changed.pop("status", None)
        previous_values.pop("status", None)

    if changed:
        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.BOOKING_UPDATED,
            {
                "booking_id": str(booking_id),
                "changed": changed,
                "previous_values": previous_values,
            },
        )


async def run_booking_patch_availability_refresh(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_before: Booking,
    booking_after: Booking | None,
) -> None:
    if booking_after is None:
        return
    rb = {ln.room_type_id for ln in booking_before.lines}
    ra = {ln.room_type_id for ln in booking_after.lines}
    if len(rb) != 1 or rb != ra:
        return
    rt_id = next(iter(rb))
    dates = sorted(
        {ln.date for ln in booking_before.lines}
        | {ln.date for ln in booking_after.lines}
    )
    await emit_availability_for_dates(factory, tenant_id, rt_id, dates)
