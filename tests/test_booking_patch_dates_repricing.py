"""PATCH booking check_in/check_out triggers repricing when rates exist."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_repricing_scenario() -> dict[str, UUID]:
    """Original stay Apr 1–4 (3 nights @ 50); rates for Apr 10–13 @ 60 for date change."""
    tenant_id = uuid4()
    user_id = uuid4()
    url = _database_url()
    assert url
    old_nights = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
    new_nights = [date(2026, 4, 10), date(2026, 4, 11), date(2026, 4, 12)]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="ReprTenant",
                    billing_email="rp@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email="rp@example.com",
                    password_hash=hash_password("secret"),
                    full_name="RP",
                    role="receptionist",
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="RP Prop",
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
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rp)
            await session.flush()
            guest = Guest(
                tenant_id=tenant_id,
                first_name="R",
                last_name="P",
                email="g@rp.example.com",
                phone="+10000000001",
            )
            session.add(guest)
            await session.flush()
            for night in old_nights + new_nights:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=night,
                        price=Decimal("50.00")
                        if night in old_nights
                        else Decimal("60.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=night,
                        total_rooms=10,
                        booked_rooms=0,
                        blocked_rooms=0,
                    ),
                )
            booking = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                rate_plan_id=rp.id,
                status="confirmed",
                source="test",
                total_amount=Decimal("150.00"),
            )
            session.add(booking)
            await session.flush()
            for night in old_nights:
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking.id,
                        date=night,
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("50.00"),
                    ),
                )
            # consume one unit of inventory on old nights
            for night in old_nights:
                await session.execute(
                    text(
                        "UPDATE availability_ledger SET booked_rooms = 1 "
                        "WHERE tenant_id = CAST(:tid AS uuid) AND room_type_id = CAST(:rt AS uuid) "
                        "AND date = :d"
                    ),
                    {
                        "tid": str(tenant_id),
                        "rt": str(rt.id),
                        "d": night,
                    },
                )
            await session.flush()
            booking_id = booking.id

    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "booking_id": booking_id,
    }


@pytest.fixture
def repricing_scenario() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed_repricing_scenario())


def test_patch_booking_dates_reprices_total(
    client,
    repricing_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = repricing_scenario["tenant_id"]
    uid = repricing_scenario["user_id"]
    bid = repricing_scenario["booking_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={
            "check_in": "2026-04-10",
            "check_out": "2026-04-13",
        },
    )
    assert r.status_code == 204
    g = client.get(f"/bookings/{bid}", headers=h)
    assert g.status_code == 200
    data = g.json()
    assert Decimal(str(data["total_amount"])) == Decimal("180.00")
    assert data["check_in_date"] == "2026-04-10"
    assert data["check_out_date"] == "2026-04-13"
