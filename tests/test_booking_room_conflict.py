"""Two bookings cannot assign the same room on overlapping nights."""

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


async def _seed_two_bookings_one_room() -> dict[str, UUID]:
    tenant_id = uuid4()
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    stay_nights = [date(2026, 7, 1), date(2026, 7, 2)]

    room_id_out: UUID | None = None
    booking_ids: list[UUID] = []

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="RoomConflictTenant",
                    billing_email="rc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="RC Prop",
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
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(room_type)
            await session.flush()
            rate_plan = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rate_plan)
            await session.flush()
            phys = Room(
                tenant_id=tenant_id,
                room_type_id=room_type.id,
                name="505",
                status="available",
            )
            session.add(phys)
            await session.flush()
            room_id_out = phys.id
            for night in stay_nights:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        rate_plan_id=rate_plan.id,
                        date=night,
                        price=Decimal("40.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        date=night,
                        total_rooms=10,
                        booked_rooms=2,
                        blocked_rooms=0,
                    ),
                )
            for i, (fn, ln, em) in enumerate(
                (
                    ("A", "One", "a1@rc.example.com"),
                    ("B", "Two", "b2@rc.example.com"),
                ),
            ):
                guest = Guest(
                    tenant_id=tenant_id,
                    first_name=fn,
                    last_name=ln,
                    email=em,
                    phone=f"+1000000000{i}",
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
                    total_amount=Decimal("80.00"),
                )
                session.add(booking)
                await session.flush()
                for night in stay_nights:
                    session.add(
                        BookingLine(
                            tenant_id=tenant_id,
                            booking_id=booking.id,
                            date=night,
                            room_type_id=room_type.id,
                            room_id=None,
                            price_for_date=Decimal("40.00"),
                        ),
                    )
                await session.flush()
                booking_ids.append(booking.id)

    await engine.dispose()
    assert room_id_out is not None and len(booking_ids) == 2
    return {
        "tenant_id": tenant_id,
        "booking_a": booking_ids[0],
        "booking_b": booking_ids[1],
        "room_id": room_id_out,
    }


@pytest.fixture
def room_conflict_scenario() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")
    return asyncio.run(_seed_two_bookings_one_room())


def test_second_assign_same_room_rejected(
    client,
    room_conflict_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = room_conflict_scenario["tenant_id"]
    ba = room_conflict_scenario["booking_a"]
    bb = room_conflict_scenario["booking_b"]
    rid = room_conflict_scenario["room_id"]
    h = auth_headers(tid, role="receptionist")
    r_ok = client.patch(
        f"/bookings/{ba}",
        headers=h,
        json={"room_id": str(rid)},
    )
    assert r_ok.status_code == 204
    r_fail = client.patch(
        f"/bookings/{bb}",
        headers=h,
        json={"room_id": str(rid)},
    )
    assert r_fail.status_code == 409
    assert "room" in r_fail.json()["detail"].lower()
