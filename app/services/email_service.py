"""Transactional email orchestration (Resend + email_logs)."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.rls_session import tenant_transaction_session
from app.integrations.resend.client import send_email
from app.integrations.resend.renderer import render_email
from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.notifications.email_log import EmailLog
from app.models.rates.rate_plan import RatePlan
from app.services.channex_booking_service import ChannexIngestResult
from app.services.folio_service import list_folio_transactions
from app.services.pdf_invoice_service import generate_invoice_pdf
from app.services.tax_service import get_tax_config

log = structlog.get_logger()


def _guest_may_receive_email(guest: Guest | None) -> bool:
    if guest is None or not (guest.email or "").strip():
        return False
    return not guest.email.strip().lower().endswith(".invalid")


async def _log_email(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    property_id: UUID | None,
    booking_id: UUID | None,
    to_address: str,
    template_name: str,
    subject: str,
    status: str,
    resend_id: str | None,
    error_message: str | None,
) -> None:
    row = EmailLog(
        tenant_id=tenant_id,
        property_id=property_id,
        booking_id=booking_id,
        to_address=to_address,
        template_name=template_name,
        subject=subject,
        status=status,
        resend_id=resend_id,
        error_message=error_message,
    )
    session.add(row)
    await session.flush()


async def send_booking_email(
    session: AsyncSession,
    tenant_id: UUID,
    to: str,
    subject: str,
    html: str,
    *,
    property_id: UUID | None = None,
    booking_id: UUID | None = None,
    template_name: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> None:
    settings = get_settings()
    if not (settings.resend_api_key or "").strip():
        log.warning(
            "email_skipped_no_api_key",
            to=to,
            subject=subject,
            template_name=template_name,
        )
        return
    try:
        resend_id = await send_email(
            [to],
            subject=subject,
            html=html,
            attachments=attachments,
        )
    except Exception as exc:
        log.error(
            "email_send_failed",
            to=to,
            error=str(exc),
            template=template_name,
        )
        await _log_email(
            session,
            tenant_id=tenant_id,
            property_id=property_id,
            booking_id=booking_id,
            to_address=to,
            template_name=template_name,
            subject=subject,
            status="failed",
            resend_id=None,
            error_message=str(exc)[:8000],
        )
        return
    await _log_email(
        session,
        tenant_id=tenant_id,
        property_id=property_id,
        booking_id=booking_id,
        to_address=to,
        template_name=template_name,
        subject=subject,
        status="sent",
        resend_id=resend_id,
        error_message=None,
    )


def _stay_summary(
    lines: list[Any],
) -> tuple[str, str, int]:
    if not lines:
        return "", "", 0
    nights = sorted({ln.date for ln in lines})
    ci: date = min(nights)
    co: date = max(nights) + timedelta(days=1)
    return ci.isoformat(), co.isoformat(), len(nights)


async def _room_type_name(session: AsyncSession, tenant_id: UUID, lines: list[Any]) -> str:
    if not lines:
        return ""
    rt_id = lines[0].room_type_id
    row = await session.scalar(
        select(RoomType.name).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == rt_id,
        ),
    )
    return (row or "").strip() or "Room"


async def _room_label(session: AsyncSession, tenant_id: UUID, lines: list[Any]) -> str | None:
    for ln in lines:
        if ln.room_id is not None:
            r = await session.scalar(
                select(Room).where(
                    Room.tenant_id == tenant_id,
                    Room.id == ln.room_id,
                ),
            )
            if r is not None:
                return str(r.name)
    return None


async def send_booking_confirmation(
    session: AsyncSession,
    tenant_id: UUID,
    booking: Booking,
    property_: Property,
    guest: Guest,
) -> None:
    if not _guest_may_receive_email(guest):
        return
    lines = list(booking.lines) if booking.lines else []
    ci, co, n_nights = _stay_summary(lines)
    rt_name = await _room_type_name(session, tenant_id, lines)
    room_name = await _room_label(session, tenant_id, lines)
    ctx: dict[str, Any] = {
        "guest_name": f"{guest.first_name} {guest.last_name}".strip(),
        "booking_reference": str(booking.id)[:8].upper(),
        "check_in": ci,
        "check_out": co,
        "nights": n_nights,
        "room_type_name": rt_name,
        "room_name": room_name or "",
        "total_amount": format(booking.total_amount, "f"),
        "currency": property_.currency,
        "property": {
            "name": property_.name,
            "address": "",
            "phone": "",
        },
    }
    html = render_email("booking_confirmation.html", ctx)
    subj = f"Booking confirmed — {property_.name}"
    await send_booking_email(
        session,
        tenant_id,
        guest.email.strip(),
        subj,
        html,
        property_id=booking.property_id,
        booking_id=booking.id,
        template_name="booking_confirmation",
    )


async def send_cancellation_email(
    session: AsyncSession,
    tenant_id: UUID,
    booking: Booking,
    property_: Property,
    guest: Guest,
) -> None:
    if not _guest_may_receive_email(guest):
        return
    lines = list(booking.lines) if booking.lines else []
    ci, co, _n = _stay_summary(lines)
    rt_name = await _room_type_name(session, tenant_id, lines)
    policy_line = ""
    if booking.rate_plan_id:
        pol = await session.scalar(
            select(RatePlan.cancellation_policy).where(
                RatePlan.tenant_id == tenant_id,
                RatePlan.id == booking.rate_plan_id,
            ),
        )
        if pol:
            policy_line = pol.strip()[:2000]
    ctx = {
        "guest_name": f"{guest.first_name} {guest.last_name}".strip(),
        "booking_reference": str(booking.id)[:8].upper(),
        "check_in": ci,
        "check_out": co,
        "room_type_name": rt_name,
        "cancellation_policy": policy_line,
        "property": {
            "name": property_.name,
            "address": "",
            "phone": "",
        },
    }
    html = render_email("booking_cancellation.html", ctx)
    subj = f"Booking cancelled — {property_.name}"
    await send_booking_email(
        session,
        tenant_id,
        guest.email.strip(),
        subj,
        html,
        property_id=booking.property_id,
        booking_id=booking.id,
        template_name="booking_cancellation",
    )


async def send_checkin_reminder_email(
    session: AsyncSession,
    tenant_id: UUID,
    booking: Booking,
    property_: Property,
    guest: Guest,
) -> None:
    if booking.status != "confirmed":
        return
    if not _guest_may_receive_email(guest):
        return
    lines = list(booking.lines) if booking.lines else []
    ci, co, _ = _stay_summary(lines)
    rt_name = await _room_type_name(session, tenant_id, lines)
    room_name = await _room_label(session, tenant_id, lines)
    ctx = {
        "guest_name": f"{guest.first_name} {guest.last_name}".strip(),
        "booking_reference": str(booking.id)[:8].upper(),
        "check_in": ci,
        "check_out": co,
        "room_type_name": rt_name,
        "room_name": room_name or "",
        "property": {
            "name": property_.name,
            "address": "",
            "phone": "",
        },
    }
    html = render_email("checkin_reminder.html", ctx)
    subj = f"Your check-in is tomorrow — {property_.name}"
    await send_booking_email(
        session,
        tenant_id,
        guest.email.strip(),
        subj,
        html,
        property_id=booking.property_id,
        booking_id=booking.id,
        template_name="checkin_reminder",
    )


async def send_invoice_email_for_booking(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    *,
    to_override: str | None = None,
) -> None:
    booking = await session.scalar(
        select(Booking)
        .options(selectinload(Booking.guest), selectinload(Booking.lines))
        .where(Booking.tenant_id == tenant_id, Booking.id == booking_id),
    )
    if booking is None or booking.guest is None:
        msg = "booking not found"
        raise ValueError(msg)
    property_ = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == booking.property_id,
        ),
    )
    if property_ is None:
        msg = "property not found"
        raise ValueError(msg)
    guest = booking.guest
    target = (to_override or "").strip() or (guest.email or "").strip()
    if not target or target.lower().endswith(".invalid"):
        msg = "guest has no valid email"
        raise ValueError(msg)
    folio_lines, _balance = await list_folio_transactions(
        session,
        tenant_id,
        booking_id,
    )
    tax = await get_tax_config(session, tenant_id, booking.property_id)
    pdf = await generate_invoice_pdf(
        booking,
        list(folio_lines),
        tax,
        property_,
        guest=guest,
        booking_lines=list(booking.lines) if booking.lines else None,
        room_name=await _room_label(session, tenant_id, list(booking.lines or [])),
        generated_at=datetime.now(timezone.utc),
    )
    b64 = base64.b64encode(pdf).decode("ascii")
    short = str(booking.id).replace("-", "")[:8]
    attachments: list[dict[str, Any]] = [
        {"filename": f"invoice-{short}.pdf", "content": b64},
    ]
    ctx = {
        "guest_name": f"{guest.first_name} {guest.last_name}".strip(),
        "booking_reference": str(booking.id)[:8].upper(),
        "amount_due": format(booking.total_amount, "f"),
        "currency": property_.currency,
        "property_name": property_.name,
    }
    html = render_email("invoice_email.html", ctx)
    subj = f"Invoice — {property_.name}"
    await send_booking_email(
        session,
        tenant_id,
        target,
        subj,
        html,
        property_id=booking.property_id,
        booking_id=booking.id,
        template_name="invoice_email",
        attachments=attachments,
    )


async def run_send_booking_confirmation_task(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_id: UUID,
) -> None:
    async with tenant_transaction_session(factory, tenant_id) as session:
        booking = await session.scalar(
            select(Booking)
            .options(selectinload(Booking.guest), selectinload(Booking.lines))
            .where(Booking.tenant_id == tenant_id, Booking.id == booking_id),
        )
        if booking is None or booking.guest is None:
            return
        property_ = await session.scalar(
            select(Property).where(
                Property.tenant_id == tenant_id,
                Property.id == booking.property_id,
            ),
        )
        if property_ is None:
            return
        await send_booking_confirmation(
            session,
            tenant_id,
            booking,
            property_,
            booking.guest,
        )


async def run_send_cancellation_email_task(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    booking_id: UUID,
) -> None:
    async with tenant_transaction_session(factory, tenant_id) as session:
        booking = await session.scalar(
            select(Booking)
            .options(selectinload(Booking.guest), selectinload(Booking.lines))
            .where(Booking.tenant_id == tenant_id, Booking.id == booking_id),
        )
        if booking is None or booking.guest is None:
            return
        property_ = await session.scalar(
            select(Property).where(
                Property.tenant_id == tenant_id,
                Property.id == booking.property_id,
            ),
        )
        if property_ is None:
            return
        await send_cancellation_email(
            session,
            tenant_id,
            booking,
            property_,
            booking.guest,
        )


async def dispatch_channex_booking_emails(
    factory: async_sessionmaker[AsyncSession],
    ingest_out: ChannexIngestResult,
) -> None:
    if not ingest_out.success:
        return
    if ingest_out.email_confirmation_booking_id is not None:
        await run_send_booking_confirmation_task(
            factory,
            ingest_out.tenant_id,
            ingest_out.email_confirmation_booking_id,
        )
    if ingest_out.email_cancellation_booking_id is not None:
        await run_send_cancellation_email_task(
            factory,
            ingest_out.tenant_id,
            ingest_out.email_cancellation_booking_id,
        )
