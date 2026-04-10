"""Create booking auto-assigns first free physical room when one exists."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.auth.user import User
from app.models.bookings.booking_line import BookingLine
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def auto_room_ctx() -> dict[str, object]:
    if not _database_url():
        pytest.skip("DATABASE_URL not set")
    tenant_id = uuid4()
    user_id = uuid4()
    url = _database_url()
    assert url
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    stay = [date(2026, 6, 1), date(2026, 6, 2)]

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="AutoRoomTenant",
                        billing_email="ar@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=user_id,
                        tenant_id=tenant_id,
                        email="owner@auto.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Auto Prop",
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
                session.add(
                    Room(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        name="108",
                        status="available",
                    ),
                )
                session.add(
                    Room(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        name="107",
                        status="available",
                    ),
                )
                await session.flush()
                for night in stay:
                    session.add(
                        Rate(
                            tenant_id=tenant_id,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=night,
                            price=Decimal("80.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tenant_id,
                            room_type_id=rt.id,
                            date=night,
                            total_rooms=5,
                            booked_rooms=0,
                            blocked_rooms=0,
                        ),
                    )
                return {
                    "property_id": prop.id,
                    "room_type_id": rt.id,
                    "rate_plan_id": rp.id,
                    "expected_room_name": "107",
                }

    ids = asyncio.run(_seed())
    from datetime import UTC, datetime, timedelta

    token = jwt.encode(
        {
            "tenant_id": str(tenant_id),
            "sub": str(user_id),
            "role": "owner",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    with TestClient(app, base_url="http://test") as client:
        ids["client"] = client
        ids["headers"] = {"Authorization": f"Bearer {token}"}
        ids["tenant_id"] = tenant_id
        yield ids
    asyncio.run(engine.dispose())


def test_create_booking_auto_assigns_first_free_room_by_name(
    auto_room_ctx: dict[str, object],
) -> None:
    client: TestClient = auto_room_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = auto_room_ctx["headers"]  # type: ignore[assignment]
    pid = auto_room_ctx["property_id"]
    rtid = auto_room_ctx["room_type_id"]
    rpid = auto_room_ctx["rate_plan_id"]
    r = client.post(
        "/bookings",
        headers=headers,
        json={
            "property_id": str(pid),
            "room_type_id": str(rtid),
            "rate_plan_id": str(rpid),
            "check_in": "2026-06-01",
            "check_out": "2026-06-03",
            "guest": {
                "first_name": "Ann",
                "last_name": "Auto",
                "email": "ann@example.com",
                "phone": "+1",
            },
            "status": "confirmed",
            "source": "test",
        },
    )
    assert r.status_code == 201, r.text
    bid = UUID(str(r.json()["booking_id"]))
    url = _database_url()
    assert url
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _check() -> None:
        async with factory() as session:
            result = await session.execute(
                select(BookingLine.room_id, Room.name)
                .join(
                    Room,
                    (Room.tenant_id == BookingLine.tenant_id)
                    & (Room.id == BookingLine.room_id),
                )
                .where(BookingLine.booking_id == bid),
            )
            rows = result.all()
            assert len(rows) == 2
            names = {row[1] for row in rows}
            assert names == {"107"}

    asyncio.run(_check())
    asyncio.run(engine.dispose())
