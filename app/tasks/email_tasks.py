"""Celery tasks: scheduled guest emails (check-in reminders)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.rls_session import tenant_transaction_session
from app.db.session import create_async_engine_and_sessionmaker
from app.models.bookings.booking import Booking
from app.models.core.property import Property
from app.services.email_service import send_checkin_reminder_email
from app.worker import celery_app

log = structlog.get_logger()


async def _send_checkin_reminders_async() -> None:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        target_date = datetime.now(timezone.utc).date() + timedelta(days=1)
        async with factory() as session:
            res = await session.execute(
                text(
                    "SELECT tenant_id, booking_id FROM "
                    "lookup_checkin_reminder_candidates(:for_date)",
                ),
                {"for_date": target_date},
            )
            rows = res.fetchall()

        for row in rows:
            tid = UUID(str(row[0]))
            bid = UUID(str(row[1]))
            try:
                async with tenant_transaction_session(factory, tid) as tses:
                    booking = await tses.scalar(
                        select(Booking)
                        .options(
                            selectinload(Booking.guest),
                            selectinload(Booking.lines),
                        )
                        .where(Booking.tenant_id == tid, Booking.id == bid),
                    )
                    if booking is None or booking.guest is None:
                        continue
                    prop = await tses.scalar(
                        select(Property).where(
                            Property.tenant_id == tid,
                            Property.id == booking.property_id,
                        ),
                    )
                    if prop is None:
                        continue
                    await send_checkin_reminder_email(
                        tses,
                        tid,
                        booking,
                        prop,
                        booking.guest,
                    )
            except Exception as exc:
                log.warning(
                    "checkin_reminder_send_failed",
                    booking_id=str(bid),
                    tenant_id=str(tid),
                    error=str(exc),
                )
    finally:
        await engine.dispose()


@celery_app.task(name="send_checkin_reminders")
def send_checkin_reminders() -> None:
    asyncio.run(_send_checkin_reminders_async())
