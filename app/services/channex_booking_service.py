"""Ingest Channex booking revisions into OpenPMS (OTA → PMS)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.channex.schemas import ChannexBookingRevisionPayload
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.schemas.bookings import GuestPayload
from app.services.availability_lock import (
    InsufficientInventoryError,
    LedgerNotSeededError,
    decrement_booked_rooms,
    increment_booked_rooms,
    lock_and_validate_availability,
)
from app.services.booking_service import (
    InvalidBookingContextError,
    _pick_first_free_room_for_stay,
    assign_booking_room,
)
from app.services.folio_service import replace_country_pack_tax_charges
from app.services.stay_dates import iter_stay_nights

log = structlog.get_logger()


def _parse_iso_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _decimal_amount(raw: str | float | int | None) -> Decimal:
    if raw is None:
        return Decimal("0.00")
    if isinstance(raw, Decimal):
        return raw.quantize(Decimal("0.01"))
    if isinstance(raw, (int, float)):
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    s = str(raw).strip()
    if not s:
        return Decimal("0.00")
    return Decimal(s).quantize(Decimal("0.01"))


def _nightly_prices_from_total(
    total: Decimal,
    nights: list[date],
) -> list[tuple[date, Decimal]]:
    n = len(nights)
    if n == 0:
        return []
    penny = Decimal("0.01")
    unit = (total / n).quantize(penny, rounding=ROUND_DOWN)
    first_sum = unit * (n - 1)
    last_night = (total - first_sum).quantize(penny)
    out: list[tuple[date, Decimal]] = [(nights[i], unit) for i in range(n - 1)]
    out.append((nights[-1], last_night))
    return out


def _guest_payload_from_channex(
    payload: ChannexBookingRevisionPayload,
    *,
    fallback_key: str,
) -> GuestPayload:
    c = payload.customer
    first = (c.name if c else None) or "Guest"
    last = (c.surname if c else None) or "Channex"
    mail_raw = (
        c.mail if c else None
    ) or f"channex-{fallback_key}@guests.openpms.invalid"
    mail = mail_raw.strip().lower()[:320]
    phone_raw = (c.phone if c else None) or "+10000000000"
    phone = phone_raw.strip()[:64]
    return GuestPayload(
        first_name=first[:255],
        last_name=last[:255],
        email=mail,
        phone=phone,
        passport_data=None,
    )


def _source_channel(payload: ChannexBookingRevisionPayload) -> str:
    ch = (payload.channel_id or "ota").strip()[:40]
    raw = f"channex:{ch}"
    return raw[:64]


def _check_in_out(
    payload: ChannexBookingRevisionPayload,
) -> tuple[date | None, date | None]:
    room0 = payload.rooms[0] if payload.rooms else None
    if room0 is not None and room0.checkin_date and room0.checkout_date:
        ci = _parse_iso_date(room0.checkin_date)
        co = _parse_iso_date(room0.checkout_date)
        return ci, co
    return _parse_iso_date(payload.arrival_date), _parse_iso_date(
        payload.departure_date
    )


async def _resolve_room_and_rate_maps(
    session: AsyncSession,
    tenant_id: UUID,
    link_id: UUID,
    cx_room_type_id: str,
    cx_rate_plan_id: str,
) -> tuple[ChannexRoomTypeMap, ChannexRatePlanMap] | None:
    cx_rt = cx_room_type_id.strip()
    cx_rp = cx_rate_plan_id.strip()
    rtm = await session.scalar(
        select(ChannexRoomTypeMap).where(
            ChannexRoomTypeMap.tenant_id == tenant_id,
            ChannexRoomTypeMap.property_link_id == link_id,
            ChannexRoomTypeMap.channex_room_type_id == cx_rt,
        ),
    )
    if rtm is None:
        return None
    rpm = await session.scalar(
        select(ChannexRatePlanMap).where(
            ChannexRatePlanMap.tenant_id == tenant_id,
            ChannexRatePlanMap.room_type_map_id == rtm.id,
            ChannexRatePlanMap.channex_rate_plan_id == cx_rp,
        ),
    )
    if rpm is None:
        return None
    return rtm, rpm


@dataclass(frozen=True)
class ChannexIngestResult:
    skip_idempotent: bool
    schedule_availability_push: bool
    tenant_id: UUID
    property_id: UUID
    room_type_id: UUID | None
    date_strs: tuple[str, ...]
    success: bool = True
    email_confirmation_booking_id: UUID | None = None
    email_cancellation_booking_id: UUID | None = None


def _sorted_date_strs(dates: list[date]) -> tuple[str, ...]:
    return tuple(sorted({d.isoformat() for d in dates}))


async def _claim_revision_row(
    session: AsyncSession,
    tenant_id: UUID,
    link_row: ChannexPropertyLink,
    revision_id: str,
    revision_flat: dict[str, Any],
    payload: ChannexBookingRevisionPayload,
) -> ChannexBookingRevision | Literal["skip"]:
    """Insert revision row or return existing; skip if already done/processing."""
    channel = (payload.channel_id or "")[:50] or None
    ins_stmt = (
        insert(ChannexBookingRevision)
        .values(
            id=uuid4(),
            tenant_id=tenant_id,
            property_link_id=link_row.id,
            channex_revision_id=revision_id,
            channex_booking_id=(payload.booking_id or "").strip() or None,
            status=(payload.status or "")[:20] or None,
            channel_code=channel,
            payload=revision_flat,
            processing_status="processing",
        )
        .on_conflict_do_nothing(index_elements=["channex_revision_id"])
        .returning(ChannexBookingRevision.id)
    )
    res = await session.execute(ins_stmt)
    inserted_id: UUID | None = res.scalar_one_or_none()

    rev_row = await session.scalar(
        select(ChannexBookingRevision).where(
            ChannexBookingRevision.channex_revision_id == revision_id,
        ),
    )
    if rev_row is None:
        log.error("channex_revision_row_missing", revision_id=revision_id)
        raise RuntimeError("channex_booking_revision insert failed")

    if inserted_id is None:
        if rev_row.processing_status in ("done", "processing"):
            return "skip"
        if rev_row.processing_status == "error":
            rev_row.processing_status = "processing"
            rev_row.error_message = None
            rev_row.payload = revision_flat
            await session.flush()

    return rev_row


async def ingest_channex_booking(
    session: AsyncSession,
    tenant_id: UUID,
    link_row: ChannexPropertyLink,
    revision_flat: dict[str, Any],
) -> ChannexIngestResult:
    """
    Apply booking revision to OpenPMS. Caller commits transaction before Channex acknowledge.
    """
    property_id = link_row.property_id
    empty_dates: tuple[str, ...] = ()

    try:
        payload = ChannexBookingRevisionPayload.model_validate(revision_flat)
    except Exception as exc:
        log.warning("channex_booking_payload_invalid", error=str(exc))
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=None,
            date_strs=empty_dates,
            success=False,
        )

    revision_id = (payload.id or "").strip()
    if not revision_id:
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=None,
            date_strs=empty_dates,
            success=False,
        )

    claim = await _claim_revision_row(
        session,
        tenant_id,
        link_row,
        revision_id,
        revision_flat,
        payload,
    )
    now = datetime.now(timezone.utc)
    if claim == "skip":
        return ChannexIngestResult(
            skip_idempotent=True,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=None,
            date_strs=empty_dates,
        )

    rev_row: ChannexBookingRevision = claim

    status_raw = (payload.status or "").strip().lower()
    room0 = payload.rooms[0] if payload.rooms else None
    if room0 is None or not room0.room_type_id or not room0.rate_plan_id:
        rev_row.processing_status = "error"
        rev_row.error_message = (
            "Channex revision missing rooms[0] or room_type_id/rate_plan_id"
        )
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=None,
            date_strs=empty_dates,
            success=False,
        )

    maps = await _resolve_room_and_rate_maps(
        session,
        tenant_id,
        link_row.id,
        room0.room_type_id,
        room0.rate_plan_id,
    )
    if maps is None:
        rev_row.processing_status = "error"
        rev_row.error_message = (
            f"Unknown channex_room_type_id or rate map: "
            f"room_type={room0.room_type_id!r} rate_plan={room0.rate_plan_id!r}"
        )
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=None,
            date_strs=empty_dates,
            success=False,
        )

    rtm, rpm = maps
    room_type_id = rtm.room_type_id
    rate_plan_id = rpm.rate_plan_id

    cx_booking_external = (payload.booking_id or "").strip() or revision_id

    check_in, check_out = _check_in_out(payload)
    if check_in is None or check_out is None or check_out <= check_in:
        rev_row.processing_status = "error"
        rev_row.error_message = "Invalid or missing arrival/departure dates"
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    nights = list(iter_stay_nights(check_in, check_out))
    if not nights:
        rev_row.processing_status = "error"
        rev_row.error_message = "Empty stay nights"
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    total = _decimal_amount(payload.amount)
    per_night = _nightly_prices_from_total(total, nights)
    source = _source_channel(payload)
    guest_pl = _guest_payload_from_channex(payload, fallback_key=cx_booking_external)

    dates_for_push: list[date] = []
    email_confirmation_booking_id: UUID | None = None
    email_cancellation_booking_id: UUID | None = None

    try:
        if status_raw == "cancelled":
            booking = await session.scalar(
                select(Booking)
                .where(
                    Booking.tenant_id == tenant_id,
                    Booking.external_booking_id == cx_booking_external,
                )
                .options(selectinload(Booking.lines)),
            )
            if booking is None:
                rev_row.processing_status = "error"
                rev_row.error_message = f"No OpenPMS booking for external_booking_id={cx_booking_external!r}"
                rev_row.processed_at = now
                await session.flush()
                return ChannexIngestResult(
                    skip_idempotent=False,
                    schedule_availability_push=False,
                    tenant_id=tenant_id,
                    property_id=property_id,
                    room_type_id=room_type_id,
                    date_strs=empty_dates,
                    success=False,
                )
            if booking.status != "cancelled":
                lines = list(booking.lines)
                if lines:
                    rt_ids = {ln.room_type_id for ln in lines}
                    if len(rt_ids) == 1:
                        rt_id = next(iter(rt_ids))
                        old_nights = [ln.date for ln in lines]
                        rows = await lock_and_validate_availability(
                            session,
                            tenant_id,
                            rt_id,
                            old_nights,
                            rooms_to_book=0,
                        )
                        decrement_booked_rooms(rows, 1)
                        dates_for_push.extend(old_nights)
                booking.status = "cancelled"
                email_cancellation_booking_id = booking.id
            rev_row.openpms_booking_id = booking.id
            rev_row.processing_status = "done"
            rev_row.error_message = None
            rev_row.processed_at = now
            link_row.last_sync_at = now  # type: ignore[assignment]
            await session.flush()

        elif status_raw == "modified":
            booking = await session.scalar(
                select(Booking)
                .where(
                    Booking.tenant_id == tenant_id,
                    Booking.external_booking_id == cx_booking_external,
                )
                .options(selectinload(Booking.lines)),
            )
            if booking is None:
                rev_row.processing_status = "error"
                rev_row.error_message = f"No OpenPMS booking for external_booking_id={cx_booking_external!r}"
                rev_row.processed_at = now
                await session.flush()
                return ChannexIngestResult(
                    skip_idempotent=False,
                    schedule_availability_push=False,
                    tenant_id=tenant_id,
                    property_id=property_id,
                    room_type_id=room_type_id,
                    date_strs=empty_dates,
                    success=False,
                )
            old_lines = list(booking.lines)
            old_nights = sorted({ln.date for ln in old_lines})
            old_rt = {ln.room_type_id for ln in old_lines}
            if len(old_rt) != 1:
                rev_row.processing_status = "error"
                rev_row.error_message = (
                    "Booking lines must share one room type for Channex modify"
                )
                rev_row.processed_at = now
                await session.flush()
                return ChannexIngestResult(
                    skip_idempotent=False,
                    schedule_availability_push=False,
                    tenant_id=tenant_id,
                    property_id=property_id,
                    room_type_id=room_type_id,
                    date_strs=empty_dates,
                    success=False,
                )
            old_room_type_id = next(iter(old_rt))

            old_rows = await lock_and_validate_availability(
                session,
                tenant_id,
                old_room_type_id,
                old_nights,
                rooms_to_book=0,
            )
            decrement_booked_rooms(old_rows, 1)
            dates_for_push.extend(old_nights)

            new_rows = await lock_and_validate_availability(
                session,
                tenant_id,
                room_type_id,
                nights,
                rooms_to_book=1,
            )
            increment_booked_rooms(new_rows, 1)
            dates_for_push.extend(nights)

            await session.execute(
                delete(BookingLine).where(
                    BookingLine.tenant_id == tenant_id,
                    BookingLine.booking_id == booking.id,
                ),
            )
            booking.rate_plan_id = rate_plan_id
            booking.total_amount = total
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
            await session.flush()
            await _update_room_charge_folio(session, tenant_id, booking.id, total)
            await replace_country_pack_tax_charges(
                session,
                tenant_id,
                booking.id,
                booking.property_id,
                total,
            )
            guest = await session.scalar(
                select(Guest).where(
                    Guest.tenant_id == tenant_id,
                    Guest.id == booking.guest_id,
                ),
            )
            if guest is not None:
                guest.first_name = guest_pl.first_name
                guest.last_name = guest_pl.last_name
                guest.email = guest_pl.email.strip().lower()
                guest.phone = guest_pl.phone
                if payload.notes:
                    guest.notes = (payload.notes or "")[:2000]

            rev_row.openpms_booking_id = booking.id
            rev_row.processing_status = "done"
            rev_row.error_message = None
            rev_row.processed_at = now
            link_row.last_sync_at = now  # type: ignore[assignment]
            await session.flush()

        else:
            # new (or unknown status treated as new)
            existing_booking = await session.scalar(
                select(Booking).where(
                    Booking.tenant_id == tenant_id,
                    Booking.external_booking_id == cx_booking_external,
                ),
            )
            if existing_booking is not None:
                rev_row.openpms_booking_id = existing_booking.id
                rev_row.processing_status = "done"
                rev_row.error_message = None
                rev_row.processed_at = now
                link_row.last_sync_at = now  # type: ignore[assignment]
                await session.flush()
                return ChannexIngestResult(
                    skip_idempotent=False,
                    schedule_availability_push=False,
                    tenant_id=tenant_id,
                    property_id=property_id,
                    room_type_id=room_type_id,
                    date_strs=empty_dates,
                )

            ledger_rows = await lock_and_validate_availability(
                session,
                tenant_id,
                room_type_id,
                nights,
                rooms_to_book=1,
            )
            increment_booked_rooms(ledger_rows, 1)
            dates_for_push.extend(nights)

            from app.services.booking_service import _get_or_create_guest_for_booking

            guest, _ = await _get_or_create_guest_for_booking(
                session,
                tenant_id,
                guest_pl,
                force_new_guest=False,
            )
            if payload.notes and guest.notes is None:
                guest.notes = (payload.notes or "")[:2000]

            booking = Booking(
                tenant_id=tenant_id,
                property_id=property_id,
                guest_id=guest.id,
                rate_plan_id=rate_plan_id,
                status="confirmed",
                source=source,
                total_amount=total,
                external_booking_id=cx_booking_external[:128],
            )
            session.add(booking)
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
            session.add(
                FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    transaction_type="Charge",
                    amount=total,
                    payment_method=None,
                    description="Room charge (Channex OTA)",
                    created_by=None,
                    category="room_charge",
                ),
            )
            await session.flush()
            await replace_country_pack_tax_charges(
                session,
                tenant_id,
                booking.id,
                property_id,
                total,
            )
            night_list = [d for d, _ in per_night]
            picked = await _pick_first_free_room_for_stay(
                session,
                tenant_id,
                property_id,
                room_type_id,
                night_list,
                booking.id,
            )
            if picked is not None:
                await assign_booking_room(
                    session,
                    tenant_id,
                    booking.id,
                    picked,
                )

            rev_row.openpms_booking_id = booking.id
            rev_row.processing_status = "done"
            rev_row.error_message = None
            rev_row.processed_at = now
            link_row.last_sync_at = now  # type: ignore[assignment]
            await session.flush()
            email_confirmation_booking_id = booking.id

    except InsufficientInventoryError as exc:
        log.warning(
            "channex_overbooking",
            revision_id=revision_id,
            error=str(exc),
        )
        rev_row.processing_status = "error"
        rev_row.error_message = f"overbooking: {exc}"[:2000]
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    except LedgerNotSeededError as exc:
        log.warning(
            "channex_ledger_not_seeded",
            revision_id=revision_id,
            error=str(exc),
        )
        rev_row.processing_status = "error"
        rev_row.error_message = f"ledger not seeded: {exc}"[:2000]
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    except InvalidBookingContextError as exc:
        log.warning(
            "channex_guest_collision",
            revision_id=revision_id,
            error=str(exc),
        )
        rev_row.processing_status = "error"
        rev_row.error_message = f"guest creation failed: {exc}"[:2000]
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    except IntegrityError as exc:
        log.warning("channex_booking_integrity", error=str(exc))
        rev_row.processing_status = "error"
        rev_row.error_message = f"integrity error: {exc}"[:2000]
        rev_row.processed_at = now
        await session.flush()
        return ChannexIngestResult(
            skip_idempotent=False,
            schedule_availability_push=False,
            tenant_id=tenant_id,
            property_id=property_id,
            room_type_id=room_type_id,
            date_strs=empty_dates,
            success=False,
        )

    return ChannexIngestResult(
        skip_idempotent=False,
        schedule_availability_push=True,
        tenant_id=tenant_id,
        property_id=property_id,
        room_type_id=room_type_id,
        date_strs=_sorted_date_strs(dates_for_push),
        email_confirmation_booking_id=email_confirmation_booking_id,
        email_cancellation_booking_id=email_cancellation_booking_id,
    )


async def _update_room_charge_folio(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    amount: Decimal,
) -> None:
    ft = await session.scalar(
        select(FolioTransaction)
        .where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
            FolioTransaction.transaction_type == "Charge",
            FolioTransaction.category == "room_charge",
        )
        .order_by(FolioTransaction.created_at.asc())
        .limit(1),
    )
    if ft is not None:
        ft.amount = amount
