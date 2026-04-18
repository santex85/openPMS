"""Coverage for app.services.webhook_runner (booking/rate webhooks + availability)."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core import webhook_events as ev
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.integrations.country_pack_extension import CountryPackExtension
from app.models.integrations.property_extension import PropertyExtension
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.services.webhook_runner import (
    load_booking_for_webhook,
    run_booking_created_webhook,
    run_booking_patch_availability_refresh,
    run_booking_patch_webhooks,
    run_rate_updated_webhooks,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    import os

    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_booking_with_lines(
    db_engine: object,
    *,
    with_extension: bool = False,
    second_room_type: bool = False,
) -> dict[str, UUID]:
    tenant_id = uuid4()
    guest_id = uuid4()
    booking_id = uuid4()
    stay = [date(2027, 4, 1), date(2027, 4, 2)]
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
                    name="WrCovTenant",
                    billing_email="wr@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="WR Prop",
                timezone="UTC",
                currency="USD",
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
            rt2_id: UUID | None = None
            if second_room_type:
                rt2 = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="Suite",
                    base_occupancy=2,
                    max_occupancy=4,
                )
                session.add(rt2)
                await session.flush()
                rt2_id = rt2.id
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rp)
            await session.flush()
            room = Room(
                tenant_id=tenant_id,
                room_type_id=rt.id,
                name="201",
                status="available",
            )
            session.add(room)
            await session.flush()
            for night in stay:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=night,
                        price=Decimal("90.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=night,
                        total_rooms=5,
                        booked_rooms=1,
                        blocked_rooms=0,
                    ),
                )
            session.add(
                Guest(
                    id=guest_id,
                    tenant_id=tenant_id,
                    first_name="Ann",
                    last_name="Lee",
                    email=f"guest-{guest_id}@wr.example.com",
                    phone="+10000000002",
                    nationality="US",
                    passport_data="P123",
                ),
            )
            await session.flush()
            session.add(
                Booking(
                    id=booking_id,
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=guest_id,
                    rate_plan_id=rp.id,
                    status="confirmed",
                    source="direct",
                    total_amount=Decimal("180.00"),
                ),
            )
            for night in stay:
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking_id,
                        date=night,
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("90.00"),
                    ),
                )
            if with_extension:
                ext_id = uuid4()
                session.add(
                    CountryPackExtension(
                        id=ext_id,
                        tenant_id=tenant_id,
                        code="wr_ext",
                        name="WR Ext",
                        country_code="US",
                        webhook_url="https://example.com/country-ext",
                        required_fields=[],
                        ui_config_schema=None,
                        is_active=True,
                    ),
                )
                await session.flush()
                session.add(
                    PropertyExtension(
                        tenant_id=tenant_id,
                        property_id=prop.id,
                        extension_id=ext_id,
                        config={"k": 1},
                        is_active=True,
                    ),
                )
            pid = prop.id
            rid = room.id
            rtid = rt.id

    out: dict[str, UUID] = {
        "tenant_id": tenant_id,
        "property_id": pid,
        "guest_id": guest_id,
        "booking_id": booking_id,
        "room_id": rid,
        "room_type_id": rtid,
    }
    if second_room_type and rt2_id is not None:
        out["room_type_id_2"] = rt2_id
    return out


@pytest.mark.asyncio
async def test_run_booking_created_webhook_happy(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_booking_created_webhook(
            factory,
            ctx["tenant_id"],
            ctx["booking_id"],
        )

    events = [c.args[2] for c in mock_dispatch.await_args_list]
    assert ev.BOOKING_CREATED in events
    assert ev.AVAILABILITY_CHANGED in events
    assert mock_dispatch.await_count >= 3


@pytest.mark.asyncio
async def test_run_booking_created_webhook_missing_booking(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_booking_created_webhook(factory, ctx["tenant_id"], uuid4())

    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_booking_patch_webhooks_checked_in(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine, with_extension=True)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.services.webhook_runner.dispatch_webhook_event",
            new_callable=AsyncMock,
        ) as mock_dispatch,
        patch(
            "app.services.webhook_runner.httpx.AsyncClient",
            return_value=mock_cm,
        ),
    ):
        await run_booking_patch_webhooks(
            factory,
            ctx["tenant_id"],
            ctx["booking_id"],
            before={"status": "confirmed"},
            after={
                "status": "checked_in",
                "guest_id": str(ctx["guest_id"]),
                "room_id": str(ctx["room_id"]),
                "check_in": "2027-04-01",
            },
            cancellation_reason=None,
            folio_balance_on_checkout=None,
        )

    guest_events = [
        c.args[2]
        for c in mock_dispatch.await_args_list
        if len(c.args) > 2 and c.args[2] == ev.GUEST_CHECKED_IN
    ]
    assert guest_events
    mock_client.post.assert_awaited()


@pytest.mark.asyncio
async def test_run_booking_patch_webhooks_checked_out(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_booking_patch_webhooks(
            factory,
            ctx["tenant_id"],
            ctx["booking_id"],
            before={"status": "checked_in"},
            after={
                "status": "checked_out",
                "guest_id": str(ctx["guest_id"]),
                "room_id": str(ctx["room_id"]),
            },
            cancellation_reason=None,
            folio_balance_on_checkout="50.00",
        )

    types = [c.args[2] for c in mock_dispatch.await_args_list]
    assert ev.GUEST_CHECKED_OUT in types
    checkout_call = next(
        c for c in mock_dispatch.await_args_list if c.args[2] == ev.GUEST_CHECKED_OUT
    )
    assert checkout_call.args[3]["folio_balance"] == "50.00"


@pytest.mark.asyncio
async def test_run_booking_patch_webhooks_booking_updated(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_booking_patch_webhooks(
            factory,
            ctx["tenant_id"],
            ctx["booking_id"],
            before={"status": "confirmed", "total_amount": "100.00"},
            after={"status": "confirmed", "total_amount": "200.00"},
            cancellation_reason=None,
            folio_balance_on_checkout=None,
        )

    updated = [
        c for c in mock_dispatch.await_args_list if c.args[2] == ev.BOOKING_UPDATED
    ]
    assert len(updated) == 1
    payload = updated[0].args[3]
    assert payload["changed"]["total_amount"] == "200.00"
    assert payload["previous_values"]["total_amount"] == "100.00"


@pytest.mark.asyncio
async def test_run_rate_updated_webhooks(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    d = date(2027, 5, 1)
    rt = ctx["room_type_id"]
    # rate_plan_id from seed — re-read
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(ctx["tenant_id"])},
        )
        b = await session.get(Booking, ctx["booking_id"])
        assert b is not None
        rpid = b.rate_plan_id
        assert rpid is not None

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_rate_updated_webhooks(
            factory,
            ctx["tenant_id"],
            [
                (rt, rpid, d, "120.00"),
                (rt, rpid, date(2027, 5, 2), "121.00"),
            ],
        )

    assert mock_dispatch.await_count == 2
    assert all(c.args[2] == ev.RATE_UPDATED for c in mock_dispatch.await_args_list)


@pytest.mark.asyncio
async def test_run_booking_patch_availability_refresh(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            b_before = await load_booking_for_webhook(
                session,
                ctx["tenant_id"],
                ctx["booking_id"],
            )
            assert b_before is not None
            assert len(b_before.lines) == 2

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            session.add(
                BookingLine(
                    tenant_id=ctx["tenant_id"],
                    booking_id=ctx["booking_id"],
                    date=date(2027, 4, 3),
                    room_type_id=ctx["room_type_id"],
                    room_id=None,
                    price_for_date=Decimal("90.00"),
                ),
            )

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            b_after = await load_booking_for_webhook(
                session,
                ctx["tenant_id"],
                ctx["booking_id"],
            )
            assert b_after is not None
            assert len(b_after.lines) == 3

    with patch(
        "app.services.webhook_runner.emit_availability_for_dates",
        new_callable=AsyncMock,
    ) as mock_emit:
        await run_booking_patch_availability_refresh(
            factory,
            ctx["tenant_id"],
            b_before,
            b_after,
        )

    mock_emit.assert_awaited_once()
    _args, kwargs = mock_emit.call_args
    dates = _args[3]
    assert set(dates) == {date(2027, 4, 1), date(2027, 4, 2), date(2027, 4, 3)}


@pytest.mark.asyncio
async def test_run_booking_patch_availability_refresh_noop(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_booking_with_lines(db_engine, second_room_type=True)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    rt2 = ctx["room_type_id_2"]
    booking_b_id = uuid4()
    guest_b = uuid4()

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            session.add(
                Guest(
                    id=guest_b,
                    tenant_id=ctx["tenant_id"],
                    first_name="Bob",
                    last_name="Other",
                    email=f"bob-{guest_b}@wr.example.com",
                    phone="+10000000003",
                ),
            )
            await session.flush()
            rp = await session.scalar(
                select(RatePlan).where(
                    RatePlan.tenant_id == ctx["tenant_id"],
                    RatePlan.property_id == ctx["property_id"],
                ),
            )
            assert rp is not None
            session.add(
                Booking(
                    id=booking_b_id,
                    tenant_id=ctx["tenant_id"],
                    property_id=ctx["property_id"],
                    guest_id=guest_b,
                    rate_plan_id=rp.id,
                    status="confirmed",
                    source="direct",
                    total_amount=Decimal("90.00"),
                ),
            )
            session.add(
                BookingLine(
                    tenant_id=ctx["tenant_id"],
                    booking_id=booking_b_id,
                    date=date(2027, 6, 1),
                    room_type_id=rt2,
                    room_id=None,
                    price_for_date=Decimal("90.00"),
                ),
            )

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(ctx["tenant_id"])},
        )
        b_main = await session.scalar(
            select(Booking)
            .where(
                Booking.tenant_id == ctx["tenant_id"],
                Booking.id == ctx["booking_id"],
            )
            .options(selectinload(Booking.lines)),
        )
        b_other = await session.scalar(
            select(Booking)
            .where(
                Booking.tenant_id == ctx["tenant_id"],
                Booking.id == booking_b_id,
            )
            .options(selectinload(Booking.lines)),
        )
        assert b_main is not None and b_other is not None

    with patch(
        "app.services.webhook_runner.emit_availability_for_dates",
        new_callable=AsyncMock,
    ) as mock_emit:
        await run_booking_patch_availability_refresh(
            factory,
            ctx["tenant_id"],
            b_main,
            b_other,
        )
    mock_emit.assert_not_awaited()

    with patch(
        "app.services.webhook_runner.emit_availability_for_dates",
        new_callable=AsyncMock,
    ) as mock_emit2:
        await run_booking_patch_availability_refresh(
            factory,
            ctx["tenant_id"],
            b_main,
            None,
        )
    mock_emit2.assert_not_awaited()
