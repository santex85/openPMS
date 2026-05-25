"""DELETE /bookings/{id}: dependent rows cleaned up (TZ-19)."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.channex.crypto import encrypt_channex_api_key
from app.models.billing.stripe_charge import StripeCharge
from app.models.billing.stripe_payment_method import StripePaymentMethod
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.notifications.email_log import EmailLog
from app.models.rates.availability_ledger import AvailabilityLedger

from tests.booking_seed import database_url


def _db_url() -> str:
    u = database_url() or os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert u
    return u


def test_delete_booking_removes_stripe_charges(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    asyncio.run(_seed_stripe_charge(tid, bid))
    assert (
        client.delete(
            f"/bookings/{bid}",
            headers=auth_headers(tid, user_id=uid, role="receptionist"),
        ).status_code
        == 204
    )
    assert asyncio.run(_count_model(StripeCharge, bid)) == 0


def test_delete_booking_nulls_stripe_payment_method_booking_id(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    pm_id = asyncio.run(_seed_stripe_pm(tid, bid))
    assert (
        client.delete(
            f"/bookings/{bid}",
            headers=auth_headers(tid, user_id=uid, role="receptionist"),
        ).status_code
        == 204
    )
    assert asyncio.run(_pm_booking_id(pm_id)) is None


def test_delete_booking_nulls_email_log_booking_id(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    log_id = asyncio.run(_seed_email_log(tid, bid))
    assert (
        client.delete(
            f"/bookings/{bid}",
            headers=auth_headers(tid, user_id=uid, role="receptionist"),
        ).status_code
        == 204
    )
    assert asyncio.run(_log_booking(log_id)) is None


def test_delete_booking_nulls_channex_revision_openpms_booking_id(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]

    rv_id = asyncio.run(_seed_channex_revision(tid, bid))
    assert (
        client.delete(
            f"/bookings/{bid}",
            headers=auth_headers(tid, user_id=uid, role="receptionist"),
        ).status_code
        == 204
    )
    assert asyncio.run(_revision_openpms(rv_id)) is None


def test_delete_booking_removes_folio_transactions(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    assert asyncio.run(_count_folio(tid, bid)) >= 1
    assert (
        client.delete(
            f"/bookings/{bid}",
            headers=auth_headers(tid, user_id=uid, role="receptionist"),
        ).status_code
        == 204
    )
    assert asyncio.run(_count_folio(tid, bid)) == 0


def test_delete_booking_cancelled_releases_no_double_inventory(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    rt, dates = asyncio.run(_booking_room_type_dates_outer(tid, bid))
    assert rt is not None
    a = asyncio.run(_sum_ledgers(tid, rt, dates))

    client.patch(f"/bookings/{bid}", headers=h, json={"status": "cancelled"})
    b = asyncio.run(_sum_ledgers(tid, rt, dates))

    client.delete(f"/bookings/{bid}", headers=h)
    c = asyncio.run(_sum_ledgers(tid, rt, dates))

    assert b == c
    assert c <= a + 5


def test_delete_booking_no_show_releases_no_double_inventory(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]

    h = auth_headers(tid, user_id=uid, role="receptionist")

    rt, dates = asyncio.run(_booking_room_type_dates_outer(tid, bid))
    assert rt is not None

    client.patch(f"/bookings/{bid}", headers=h, json={"status": "no_show"})
    before_del = asyncio.run(_sum_ledgers(tid, rt, dates))

    client.delete(f"/bookings/{bid}", headers=h)
    after_del = asyncio.run(_sum_ledgers(tid, rt, dates))

    assert before_del == after_del


async def _count_model(model, booking_id: UUID) -> int:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            n = (
                (
                    await session.execute(
                        select(func.count()).select_from(model).where(
                            getattr(model, "booking_id") == booking_id,
                        ),
                    )
                ).scalar_one()
                or 0
            )
            return int(n)
    finally:
        await engine.dispose()


async def _count_folio(tenant_id: UUID, booking_id: UUID) -> int:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                ),
                {"tid": str(tenant_id)},
            )
            n = (
                (
                    await session.execute(
                        select(func.count()).select_from(FolioTransaction).where(
                            FolioTransaction.tenant_id == tenant_id,
                            FolioTransaction.booking_id == booking_id,
                        ),
                    )
                ).scalar_one()
                or 0
            )
            return int(n)
    finally:
        await engine.dispose()


async def _seed_stripe_charge(tenant_id: UUID, booking_id: UUID) -> None:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                bk = await session.get(Booking, booking_id)
                assert bk is not None
                ftid = uuid4()
                session.add(
                    FolioTransaction(
                        id=ftid,
                        tenant_id=tenant_id,
                        booking_id=booking_id,
                        transaction_type="Payment",
                        amount=Decimal("1.00"),
                        payment_method="card",
                        description="test",
                        created_by=None,
                        category="payment",
                    ),
                )
                await session.flush()
                session.add(
                    StripeCharge(
                        tenant_id=tenant_id,
                        property_id=bk.property_id,
                        booking_id=booking_id,
                        folio_tx_id=ftid,
                        stripe_charge_id=f"ch_tz19_{uuid4().hex}",
                        amount=Decimal("1.00"),
                        currency="USD",
                        status="succeeded",
                    ),
                )
    finally:
        await engine.dispose()


async def _seed_stripe_pm(tenant_id: UUID, booking_id: UUID) -> UUID:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        pm_id = uuid4()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                bk = await session.get(Booking, booking_id)
                assert bk is not None
                session.add(
                    StripePaymentMethod(
                        id=pm_id,
                        tenant_id=tenant_id,
                        property_id=bk.property_id,
                        booking_id=booking_id,
                        stripe_pm_id=f"pm_tz19_{uuid4().hex}",
                        card_last4="4242",
                    ),
                )
        return pm_id
    finally:
        await engine.dispose()


async def _pm_booking_id(pm_id: UUID) -> UUID | None:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            pm = await session.get(StripePaymentMethod, pm_id)
            return pm.booking_id if pm else None
    finally:
        await engine.dispose()


async def _seed_email_log(tenant_id: UUID, booking_id: UUID) -> UUID:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        lid = uuid4()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                bk = await session.get(Booking, booking_id)
                assert bk is not None
                session.add(
                    EmailLog(
                        id=lid,
                        tenant_id=tenant_id,
                        property_id=bk.property_id,
                        booking_id=booking_id,
                        to_address="x@y.com",
                        template_name="t",
                        subject="s",
                        status="sent",
                    ),
                )
        return lid
    finally:
        await engine.dispose()


async def _log_booking(log_id: UUID) -> UUID | None:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.get(EmailLog, log_id)
            return row.booking_id if row else None
    finally:
        await engine.dispose()


async def _seed_channex_revision(tenant_id: UUID, booking_id: UUID) -> UUID:
    settings = get_settings()
    enc = encrypt_channex_api_key(settings, "test-channex-tz19-key__________")
    rev_id = uuid4()
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                bk = await session.get(Booking, booking_id)
                assert bk is not None
                link = await session.scalar(
                    select(ChannexPropertyLink).where(
                        ChannexPropertyLink.tenant_id == tenant_id,
                        ChannexPropertyLink.property_id == bk.property_id,
                    ),
                )
                if link is None:
                    link = ChannexPropertyLink(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        property_id=bk.property_id,
                        channex_property_id=str(uuid4()),
                        channex_api_key=enc,
                        channex_env="production",
                        status="active",
                    )
                    session.add(link)
                    await session.flush()
                link_id = link.id
                session.add(
                    ChannexBookingRevision(
                        id=rev_id,
                        tenant_id=tenant_id,
                        property_link_id=link_id,
                        channex_revision_id=str(uuid4()),
                        channex_booking_id=str(uuid4()),
                        payload={"id": "x", "test": True},
                        processing_status="done",
                        openpms_booking_id=booking_id,
                    ),
                )
        return rev_id
    finally:
        await engine.dispose()


async def _revision_openpms(revision_id: UUID) -> UUID | None:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.get(ChannexBookingRevision, revision_id)
            return row.openpms_booking_id if row else None
    finally:
        await engine.dispose()


async def _booking_room_type_dates_outer(
    tenant_id: UUID,
    booking_id: UUID,
) -> tuple[UUID | None, list]:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                ),
                {"tid": str(tenant_id)},
            )
            return await _booking_room_type_dates(session, tenant_id, booking_id)
    finally:
        await engine.dispose()


async def _sum_ledgers(
    tenant_id: UUID,
    room_type_id: UUID,
    dates: list,
) -> int:
    engine = create_async_engine(_db_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                ),
                {"tid": str(tenant_id)},
            )
            return await _sum_booked(session, tenant_id, room_type_id, dates)
    finally:
        await engine.dispose()


async def _booking_room_type_dates(
    session: AsyncSession, tenant_id: UUID, booking_id: UUID
) -> tuple[UUID | None, list]:
    lines = (
        (
            await session.execute(
                select(BookingLine.room_type_id, BookingLine.date).where(
                    BookingLine.tenant_id == tenant_id,
                    BookingLine.booking_id == booking_id,
                ),
            )
        ).all()
    )
    if not lines:
        return None, []
    rt = lines[0][0]
    return rt, sorted({r[1] for r in lines})


async def _sum_booked(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    dates: list,
) -> int:
    result = (
        (
            await session.execute(
                select(func.coalesce(func.sum(AvailabilityLedger.booked_rooms), 0)).where(
                    AvailabilityLedger.tenant_id == tenant_id,
                    AvailabilityLedger.room_type_id == room_type_id,
                    AvailabilityLedger.date.in_(dates),
                ),
            )
        ).scalar_one()
    )
    return int(result)