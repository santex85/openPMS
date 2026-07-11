"""Integration tests: night audit fanout, auto no-show, email idempotency."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.audit.audit_log import AuditLog
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.notifications.email_log import EmailLog
from app.models.rates.availability_ledger import AvailabilityLedger
from app.tasks.night_audit import (
    _night_audit_fanout_async,
    _night_audit_property_async,
)
from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def _patches_send():
    return patch(
        "app.services.email_service.send_email",
        new_callable=AsyncMock,
        return_value="re_night_audit_msg",
    ), patch(
        "app.services.email_service.get_settings",
        return_value=MagicMock(resend_api_key="re_test_key", night_audit_hour=3),
    )


async def _seed_night_audit_property(
    db_engine: object,
    *,
    timezone_id: str,
    first_night: date,
    booking_status: str = "confirmed",
) -> dict[str, object]:
    tenant_id = uuid4()
    owner_id = uuid4()
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
                    name="NightAuditTenant",
                    billing_email="na-bill@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="owner@na.example.com",
                    password_hash=hash_password("Password123!"),
                    full_name="NA Owner",
                    role="owner",
                    is_active=True,
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="NA Prop",
                timezone=timezone_id,
                currency="THB",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            session.add(
                AvailabilityLedger(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    date=first_night,
                    total_rooms=1,
                    booked_rooms=1,
                    blocked_rooms=0,
                ),
            )
            guest = Guest(
                tenant_id=tenant_id,
                first_name="Late",
                last_name="Guest",
                email="late@example.com",
                phone="+66999",
            )
            session.add(guest)
            await session.flush()
            booking = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                status=booking_status,
                source="direct",
                total_amount=Decimal("100.00"),
            )
            session.add(booking)
            await session.flush()
            session.add(
                BookingLine(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    date=first_night,
                    room_type_id=rt.id,
                    price_for_date=Decimal("100.00"),
                ),
            )
            await session.flush()
            return {
                "tenant_id": tenant_id,
                "property_id": prop.id,
                "booking_id": booking.id,
                "owner_id": owner_id,
                "first_night": first_night,
            }


async def _cleanup(db_engine: object, tenant_id: UUID) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = str(tenant_id)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            for tbl, col in (
                ("email_logs", "tenant_id"),
                ("audit_log", "tenant_id"),
                ("folio_transactions", "tenant_id"),
                ("booking_lines", "tenant_id"),
                ("bookings", "tenant_id"),
                ("guests", "tenant_id"),
                ("availability_ledger", "tenant_id"),
                ("room_types", "tenant_id"),
                ("users", "tenant_id"),
                ("properties", "tenant_id"),
                ("tenants", "id"),
            ):
                await session.execute(
                    text(f"DELETE FROM {tbl} WHERE {col} = :tid"),  # noqa: S608
                    {"tid": tid},
                )


@pytest.mark.asyncio
async def test_night_audit_marks_confirmed_past_checkin_no_show(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    # First night well in the past relative to "now"
    first_night = date(2026, 7, 1)
    ids = await _seed_night_audit_property(
        db_engine,
        timezone_id="UTC",
        first_night=first_night,
    )
    tid = ids["tenant_id"]
    assert isinstance(tid, UUID)
    pid = ids["property_id"]
    assert isinstance(pid, UUID)
    bid = ids["booking_id"]
    assert isinstance(bid, UUID)
    try:
        with _patches_send()[0], _patches_send()[1]:
            # freeze local "now" to 2026-07-11 03:00 UTC
            frozen = datetime(2026, 7, 11, 3, 0, tzinfo=ZoneInfo("UTC"))
            with patch(
                "app.tasks.night_audit.property_local_now",
                return_value=frozen,
            ):
                result = await _night_audit_property_async(tid, pid)
        assert result["ok"] is True
        assert result["auto_no_shows"] == 1

        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            status = await session.scalar(
                select(Booking.status).where(
                    Booking.tenant_id == tid,
                    Booking.id == bid,
                ),
            )
            assert status == "no_show"
            audit_n = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.tenant_id == tid,
                    AuditLog.action == "booking.night_audit_no_show",
                    AuditLog.entity_id == bid,
                ),
            )
            assert int(audit_n or 0) == 1
            email_n = await session.scalar(
                select(func.count())
                .select_from(EmailLog)
                .where(
                    EmailLog.tenant_id == tid,
                    EmailLog.property_id == pid,
                    EmailLog.template_name == "night_audit",
                    EmailLog.status == "sent",
                ),
            )
            assert int(email_n or 0) == 1
    finally:
        await _cleanup(db_engine, tid)


@pytest.mark.asyncio
async def test_night_audit_idempotent_second_run(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    first_night = date(2026, 7, 1)
    ids = await _seed_night_audit_property(
        db_engine,
        timezone_id="UTC",
        first_night=first_night,
    )
    tid = ids["tenant_id"]
    assert isinstance(tid, UUID)
    pid = ids["property_id"]
    assert isinstance(pid, UUID)
    try:
        frozen = datetime(2026, 7, 11, 3, 0, tzinfo=ZoneInfo("UTC"))
        with _patches_send()[0], _patches_send()[1]:
            with patch(
                "app.tasks.night_audit.property_local_now",
                return_value=frozen,
            ):
                first = await _night_audit_property_async(tid, pid)
                second = await _night_audit_property_async(tid, pid)
        assert first["auto_no_shows"] == 1
        assert second["auto_no_shows"] == 0
        assert first["emailed"] is True
        assert second["emailed"] is False

        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            email_n = await session.scalar(
                select(func.count())
                .select_from(EmailLog)
                .where(
                    EmailLog.tenant_id == tid,
                    EmailLog.template_name == "night_audit",
                    EmailLog.status == "sent",
                ),
            )
            assert int(email_n or 0) == 1
    finally:
        await _cleanup(db_engine, tid)


@pytest.mark.asyncio
async def test_night_audit_bangkok_local_date(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    # At 2026-07-11 02:00 UTC → Asia/Bangkok is 09:00 on same calendar day.
    # First night yesterday Bangkok = 2026-07-10.
    first_night = date(2026, 7, 10)
    ids = await _seed_night_audit_property(
        db_engine,
        timezone_id="Asia/Bangkok",
        first_night=first_night,
    )
    tid = ids["tenant_id"]
    assert isinstance(tid, UUID)
    pid = ids["property_id"]
    assert isinstance(pid, UUID)
    try:
        # 20:00 UTC on July 10 = 03:00 Bangkok on July 11
        frozen = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo("UTC")).astimezone(
            ZoneInfo("Asia/Bangkok"),
        )
        assert frozen.hour == 3
        assert frozen.date() == date(2026, 7, 11)
        with _patches_send()[0], _patches_send()[1]:
            with patch(
                "app.tasks.night_audit.property_local_now",
                return_value=frozen,
            ):
                result = await _night_audit_property_async(tid, pid)
        assert result["ok"] is True
        assert result["audit_date"] == "2026-07-10"
        assert result["auto_no_shows"] == 1
    finally:
        await _cleanup(db_engine, tid)


@pytest.mark.asyncio
async def test_night_audit_fanout_hour_gating(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    first_night = date(2026, 7, 1)
    ids = await _seed_night_audit_property(
        db_engine,
        timezone_id="Asia/Bangkok",
        first_night=first_night,
    )
    tid = ids["tenant_id"]
    assert isinstance(tid, UUID)
    pid = ids["property_id"]
    assert isinstance(pid, UUID)
    try:
        settings = MagicMock(night_audit_hour=3)
        from app.core.config import get_settings as real_settings
        from app.db.session import create_async_engine_and_sessionmaker as real_mk

        real = real_settings()

        # 03:00 UTC → 10:00 Bangkok — this property must NOT be enqueued
        now_wrong = datetime(2026, 7, 11, 3, 0, tzinfo=ZoneInfo("UTC"))
        with (
            patch("app.tasks.night_audit.get_settings", return_value=settings),
            patch(
                "app.tasks.night_audit.create_async_engine_and_sessionmaker",
            ) as mk_eng,
            patch("app.tasks.night_audit.night_audit_property") as mk_task,
        ):
            engine, factory = real_mk(real)
            mk_eng.return_value = (engine, factory)
            mk_task.delay = MagicMock()
            try:
                enqueued = await _night_audit_fanout_async(now=now_wrong)
            finally:
                await engine.dispose()
        assert str(pid) not in enqueued

        # 20:00 UTC July 10 → 03:00 Bangkok July 11 — must enqueue
        now_ok = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo("UTC"))
        with (
            patch("app.tasks.night_audit.get_settings", return_value=settings),
            patch(
                "app.tasks.night_audit.create_async_engine_and_sessionmaker",
            ) as mk_eng2,
            patch("app.tasks.night_audit.night_audit_property") as mk_task2,
        ):
            engine2, factory2 = real_mk(real)
            mk_eng2.return_value = (engine2, factory2)
            mk_task2.delay = MagicMock()
            try:
                enqueued2 = await _night_audit_fanout_async(now=now_ok)
            finally:
                await engine2.dispose()
        assert str(pid) in enqueued2
        mk_task2.delay.assert_any_call(str(tid), str(pid))
    finally:
        await _cleanup(db_engine, tid)


@pytest.mark.asyncio
async def test_lookup_all_active_properties_for_worker(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ids = await _seed_night_audit_property(
        db_engine,
        timezone_id="Asia/Bangkok",
        first_night=date.today() - timedelta(days=2),
    )
    tid = ids["tenant_id"]
    assert isinstance(tid, UUID)
    pid = ids["property_id"]
    assert isinstance(pid, UUID)
    try:
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            res = await session.execute(
                text(
                    "SELECT tenant_id, property_id, timezone "
                    "FROM lookup_all_active_properties_for_worker()",
                ),
            )
            rows = {(str(r[0]), str(r[1]), str(r[2])) for r in res.fetchall()}
        assert (str(tid), str(pid), "Asia/Bangkok") in rows
    finally:
        await _cleanup(db_engine, tid)
