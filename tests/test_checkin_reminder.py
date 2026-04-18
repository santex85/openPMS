"""Integration tests: Celery check-in reminder task (lookup + email_logs)."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.notifications.email_log import EmailLog
from app.tasks.email_tasks import _send_checkin_reminders_async
from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    import os

    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_booking_for_reminder(
    db_engine: object,
    *,
    first_night: date,
    guest_email: str,
    booking_status: str,
) -> dict[str, object]:
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="ReminderTenant",
                    billing_email="bill@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Reminder Inn",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            room_type = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Standard",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(room_type)
            await session.flush()
            guest = Guest(
                tenant_id=tenant_id,
                first_name="Pat",
                last_name="Guest",
                email=guest_email,
                phone="+10000000001",
            )
            session.add(guest)
            await session.flush()
            booking = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                status=booking_status,
                source="test",
                total_amount=Decimal("100.00"),
            )
            session.add(booking)
            await session.flush()
            session.add(
                BookingLine(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    date=first_night,
                    room_type_id=room_type.id,
                    room_id=None,
                    price_for_date=Decimal("100.00"),
                ),
            )
            await session.flush()
            booking_id = booking.id
            property_id = prop.id

    return {
        "tenant_id": tenant_id,
        "booking_id": booking_id,
        "property_id": property_id,
    }


async def _delete_reminder_tenant(db_engine: object, tenant_id: UUID) -> None:
    """Delete test data in FK-safe order for the reminder seed helper."""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    tid = str(tenant_id)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            for tbl, col in (
                ("email_logs", "tenant_id"),
                ("booking_lines", "tenant_id"),
                ("bookings", "tenant_id"),
                ("guests", "tenant_id"),
                ("room_types", "tenant_id"),
                ("properties", "tenant_id"),
                ("tenants", "id"),
            ):
                await session.execute(
                    text(f"DELETE FROM {tbl} WHERE {col} = :tid"),  # noqa: S608
                    {"tid": tid},
                )


def _patches_send():
    return patch(
        "app.services.email_service.send_email",
        new_callable=AsyncMock,
        return_value="re_test_msg",
    ), patch(
        "app.services.email_service.get_settings",
        return_value=MagicMock(resend_api_key="re_test_key"),
    )


@pytest.mark.asyncio
async def test_reminder_sent_for_tomorrow_booking(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    target_ci = date(2026, 7, 15)
    ids = await _seed_booking_for_reminder(
        db_engine,
        first_night=target_ci,
        guest_email="real@example.com",
        booking_status="confirmed",
    )
    try:
        tid = ids["tenant_id"]  # type: ignore[assignment]
        bid = ids["booking_id"]  # type: ignore[assignment]

        send_m, gs = _patches_send()
        with send_m as send_email_mock, gs:
            with patch(
                "app.tasks.email_tasks.checkin_reminder_target_date",
                return_value=target_ci,
            ):
                await _send_checkin_reminders_async()

        send_email_mock.assert_awaited()
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tid)},
                )
                cnt = await session.scalar(
                    select(func.count())
                    .select_from(EmailLog)
                    .where(
                        EmailLog.booking_id == bid,
                        EmailLog.template_name == "checkin_reminder",
                        EmailLog.status == "sent",
                    ),
                )
        assert (cnt or 0) == 1
    finally:
        await _delete_reminder_tenant(db_engine, ids["tenant_id"])


@pytest.mark.asyncio
async def test_reminder_skipped_invalid_guest_email(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    target_ci = date(2026, 7, 16)
    ids = await _seed_booking_for_reminder(
        db_engine,
        first_night=target_ci,
        guest_email="ota@guests.openpms.invalid",
        booking_status="confirmed",
    )
    try:
        send_m, gs = _patches_send()
        with send_m as send_email_mock, gs:
            with patch(
                "app.tasks.email_tasks.checkin_reminder_target_date",
                return_value=target_ci,
            ):
                await _send_checkin_reminders_async()
        send_email_mock.assert_not_awaited()
    finally:
        await _delete_reminder_tenant(db_engine, ids["tenant_id"])


@pytest.mark.asyncio
async def test_reminder_skipped_cancelled_booking(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    target_ci = date(2026, 7, 17)
    ids = await _seed_booking_for_reminder(
        db_engine,
        first_night=target_ci,
        guest_email="cancel@example.com",
        booking_status="cancelled",
    )
    try:
        send_m, gs = _patches_send()
        with send_m as send_email_mock, gs:
            with patch(
                "app.tasks.email_tasks.checkin_reminder_target_date",
                return_value=target_ci,
            ):
                await _send_checkin_reminders_async()
        send_email_mock.assert_not_awaited()
    finally:
        await _delete_reminder_tenant(db_engine, ids["tenant_id"])


@pytest.mark.asyncio
async def test_reminder_not_sent_wrong_checkin_date(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ids = await _seed_booking_for_reminder(
        db_engine,
        first_night=date(2026, 9, 1),
        guest_email="later@example.com",
        booking_status="confirmed",
    )
    try:
        send_m, gs = _patches_send()
        with send_m as send_email_mock, gs:
            with patch(
                "app.tasks.email_tasks.checkin_reminder_target_date",
                return_value=date(2026, 7, 20),
            ):
                await _send_checkin_reminders_async()
        send_email_mock.assert_not_awaited()
    finally:
        await _delete_reminder_tenant(db_engine, ids["tenant_id"])


@pytest.mark.asyncio
async def test_reminder_idempotent_second_run_same_day(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    target_ci = date(2026, 7, 21)
    ids = await _seed_booking_for_reminder(
        db_engine,
        first_night=target_ci,
        guest_email="twice@example.com",
        booking_status="confirmed",
    )
    try:
        send_m, gs = _patches_send()
        with send_m as send_email_mock, gs:
            with patch(
                "app.tasks.email_tasks.checkin_reminder_target_date",
                return_value=target_ci,
            ):
                await _send_checkin_reminders_async()
                await _send_checkin_reminders_async()
        assert send_email_mock.await_count == 1
    finally:
        await _delete_reminder_tenant(db_engine, ids["tenant_id"])
