"""Build webhook payloads and dispatch (call from FastAPI BackgroundTasks)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core import webhook_events as ev
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.core.webhook_url_validation import (
    WebhookUrlUnsafeError,
    assert_webhook_target_ips_safe_for_url,
)
from app.models.core.property import Property
from app.models.integrations.country_pack_extension import CountryPackExtension
from app.models.integrations.property_extension import PropertyExtension
from app.models.rates.availability_ledger import AvailabilityLedger
from app.services.webhook_delivery_engine import dispatch_webhook_event

_log = structlog.get_logger()


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
        "notes": booking.notes,
    }


async def _post_country_pack_extension_checkin_webhooks(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_id: UUID,
    guest_payload: dict[str, object | None],
) -> None:
    """POST guest.checked_in payload to active integrator URLs (country_pack_extensions)."""
    targets: list[tuple[str, str, dict[str, object] | None]] = []
    async with factory() as session:
        async with session.begin():
            await _set_tenant(session, tenant_id)
            booking = await load_booking_for_webhook(session, tenant_id, booking_id)
            if booking is None:
                return
            res = await session.execute(
                select(
                    CountryPackExtension.webhook_url,
                    CountryPackExtension.code,
                    PropertyExtension.config,
                )
                .join(
                    PropertyExtension,
                    (PropertyExtension.extension_id == CountryPackExtension.id)
                    & (PropertyExtension.tenant_id == CountryPackExtension.tenant_id),
                )
                .where(
                    PropertyExtension.tenant_id == tenant_id,
                    PropertyExtension.property_id == booking.property_id,
                    PropertyExtension.is_active.is_(True),
                    CountryPackExtension.is_active.is_(True),
                ),
            )
            targets = [(str(u), str(c), cfg) for u, c, cfg in res.all()]

    for url, ext_code, cfg in targets:
        body: dict[str, object] = {
            "event": ev.GUEST_CHECKED_IN,
            "extension_code": ext_code,
            "data": dict(guest_payload),
        }
        if isinstance(cfg, dict):
            body["property_extension_config"] = cfg
        try:
            await asyncio.to_thread(assert_webhook_target_ips_safe_for_url, url)
        except WebhookUrlUnsafeError:
            _log.warning(
                "country_pack_extension_webhook_blocked",
                url=url,
                extension_code=ext_code,
            )
            continue
        except OSError as exc:
            _log.warning(
                "country_pack_extension_webhook_dns_failed",
                url=url,
                error=str(exc),
            )
            continue
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    json=body,
                    headers={"X-OpenPMS-Event": ev.GUEST_CHECKED_IN},
                )
                if r.status_code >= 400:
                    _log.warning(
                        "country_pack_extension_webhook_http_error",
                        url=url,
                        status_code=r.status_code,
                    )
        except httpx.HTTPError as exc:
            _log.warning(
                "country_pack_extension_webhook_failed",
                url=url,
                error=str(exc),
            )


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
        guest_payload: dict[str, object | None] = {
            "booking_id": str(booking_id),
            "guest_id": str(after.get("guest_id", "")),
            "room_id": str(after.get("room_id") or ""),
        }
        async with factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                booking = await load_booking_for_webhook(
                    session,
                    tenant_id,
                    booking_id,
                )
                if booking is not None:
                    guest_payload["property_id"] = str(booking.property_id)
                    guest = await session.scalar(
                        select(Guest).where(
                            Guest.tenant_id == tenant_id,
                            Guest.id == booking.guest_id,
                        ),
                    )
                    prop = await session.scalar(
                        select(Property).where(
                            Property.tenant_id == tenant_id,
                            Property.id == booking.property_id,
                        ),
                    )
                    if guest is not None:
                        guest_payload["first_name"] = guest.first_name
                        guest_payload["last_name"] = guest.last_name
                        guest_payload["nationality"] = guest.nationality
                        guest_payload["passport_data"] = guest.passport_data
                        guest_payload["passport_number"] = None
                        guest_payload["date_of_birth"] = (
                            guest.date_of_birth.isoformat()
                            if guest.date_of_birth
                            else None
                        )
                        guest_payload["check_in_date"] = after.get("check_in")
                        guest_payload["room_number"] = None
                    if prop is not None:
                        guest_payload["property_address"] = prop.name
                        guest_payload["property_registration_number"] = None

        await dispatch_webhook_event(
            factory,
            tenant_id,
            ev.GUEST_CHECKED_IN,
            guest_payload,
        )
        await _post_country_pack_extension_checkin_webhooks(
            factory,
            tenant_id,
            booking_id,
            guest_payload,
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
