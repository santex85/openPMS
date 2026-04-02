"""GET /rooms/for-stay lists physical rooms free on stay nights."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_two_rooms_one_busy() -> dict[str, UUID]:
    tenant_id = uuid4()
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    nights = [date(2026, 8, 1), date(2026, 8, 2)]

    busy_room_id: UUID | None = None
    free_room_id: UUID | None = None
    property_id: UUID | None = None
    room_type_id: UUID | None = None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="AssignableTenant",
                    billing_email="asg@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Asg Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            property_id = prop.id
            room_type = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(room_type)
            await session.flush()
            room_type_id = room_type.id
            rate_plan = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rate_plan)
            await session.flush()
            busy = Room(
                tenant_id=tenant_id,
                room_type_id=room_type.id,
                name="Busy-201",
                status="available",
            )
            free = Room(
                tenant_id=tenant_id,
                room_type_id=room_type.id,
                name="Free-202",
                status="available",
            )
            session.add(busy)
            session.add(free)
            await session.flush()
            busy_room_id = busy.id
            free_room_id = free.id
            for night in nights:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        rate_plan_id=rate_plan.id,
                        date=night,
                        price=Decimal("50.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        date=night,
                        total_rooms=2,
                        booked_rooms=1,
                        blocked_rooms=0,
                    ),
                )
            guest = Guest(
                tenant_id=tenant_id,
                first_name="Busy",
                last_name="Guest",
                email="busy.guest@example.com",
                phone="+10000000099",
            )
            session.add(guest)
            await session.flush()
            booking = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                rate_plan_id=rate_plan.id,
                status="confirmed",
                source="test",
                total_amount=Decimal("100.00"),
            )
            session.add(booking)
            await session.flush()
            for night in nights:
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking.id,
                        date=night,
                        room_type_id=room_type.id,
                        room_id=busy_room_id,
                        price_for_date=Decimal("50.00"),
                    ),
                )

    await engine.dispose()
    assert (
        busy_room_id is not None
        and free_room_id is not None
        and property_id is not None
        and room_type_id is not None
    )
    return {
        "tenant_id": tenant_id,
        "property_id": property_id,
        "room_type_id": room_type_id,
        "busy_room_id": busy_room_id,
        "free_room_id": free_room_id,
    }


@pytest.fixture
def assignable_rooms_scenario() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")
    return asyncio.run(_seed_two_rooms_one_busy())


def test_assignable_rooms_excludes_busy_physical_room(
    client,
    assignable_rooms_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = assignable_rooms_scenario["tenant_id"]
    pid = assignable_rooms_scenario["property_id"]
    rtid = assignable_rooms_scenario["room_type_id"]
    free = assignable_rooms_scenario["free_room_id"]
    busy = assignable_rooms_scenario["busy_room_id"]
    h = auth_headers(tid, role="receptionist")
    r = client.get(
        "/rooms/for-stay",
        headers=h,
        params={
            "property_id": str(pid),
            "room_type_id": str(rtid),
            "check_in": "2026-08-01",
            "check_out": "2026-08-03",
        },
    )
    assert r.status_code == 200
    rows = r.json()
    ids = {item["id"] for item in rows}
    assert str(free) in ids
    assert str(busy) not in ids
    assert len(rows) == 1


def test_assignable_rooms_unknown_room_type_404(
    client,
    assignable_rooms_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = assignable_rooms_scenario["tenant_id"]
    pid = assignable_rooms_scenario["property_id"]
    h = auth_headers(tid, role="receptionist")
    r = client.get(
        "/rooms/for-stay",
        headers=h,
        params={
            "property_id": str(pid),
            "room_type_id": str(uuid4()),
            "check_in": "2026-08-01",
            "check_out": "2026-08-03",
        },
    )
    assert r.status_code == 404
