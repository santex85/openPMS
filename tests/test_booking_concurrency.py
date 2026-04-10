"""Concurrent booking creation under tight inventory (may be environment-sensitive)."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_single_night_one_room() -> dict[str, UUID | date]:
    tenant_id = uuid4()
    user_id = uuid4()
    night = date(2026, 8, 1)
    url = _database_url()
    assert url
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    prop_id: UUID
    rt_id: UUID
    rp_id: UUID
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
                    name="ConcTenant",
                    billing_email="ct@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email="ct@example.com",
                    password_hash=hash_password("secret"),
                    full_name="CT",
                    role="receptionist",
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="CT Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            prop_id = prop.id
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            rt_id = rt.id
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rp)
            await session.flush()
            rp_id = rp.id
            session.add(
                Rate(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=night,
                    price=Decimal("100.00"),
                ),
            )
            session.add(
                AvailabilityLedger(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    date=night,
                    total_rooms=1,
                    booked_rooms=0,
                    blocked_rooms=0,
                ),
            )

    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "property_id": prop_id,
        "room_type_id": rt_id,
        "rate_plan_id": rp_id,
        "check_in": night,
        "check_out": night + timedelta(days=1),
    }


@pytest.fixture
def single_night_one_room_scenario() -> dict[str, UUID | date]:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed_single_night_one_room())


@pytest.mark.slow
def test_two_concurrent_bookings_one_inventory_unit(
    client,
    single_night_one_room_scenario: dict[str, UUID | date],
    auth_headers,
) -> None:
    """
    With only one bookable unit for the night, at most one POST /bookings should succeed.
    """
    tid: UUID = single_night_one_room_scenario["tenant_id"]  # type: ignore[assignment]
    uid: UUID = single_night_one_room_scenario["user_id"]  # type: ignore[assignment]
    pid: UUID = single_night_one_room_scenario["property_id"]  # type: ignore[assignment]
    rtid: UUID = single_night_one_room_scenario["room_type_id"]  # type: ignore[assignment]
    rpid: UUID = single_night_one_room_scenario["rate_plan_id"]  # type: ignore[assignment]
    ci: date = single_night_one_room_scenario["check_in"]  # type: ignore[assignment]
    co: date = single_night_one_room_scenario["check_out"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    def _post(idx: int) -> int:
        return client.post(
            "/bookings",
            headers=h,
            json={
                "property_id": str(pid),
                "room_type_id": str(rtid),
                "rate_plan_id": str(rpid),
                "check_in": ci.isoformat(),
                "check_out": co.isoformat(),
                "guest": {
                    "first_name": "C",
                    "last_name": str(idx),
                    "email": f"c{idx}_{uuid4().hex[:8]}@conc.example.com",
                    "phone": "+10000000001",
                },
                "status": "confirmed",
                "source": "test",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_post, i) for i in range(2)]
        codes = [f.result() for f in as_completed(futures)]

    assert codes.count(201) == 1, f"expected exactly one successful booking; got {codes}"
    assert codes.count(409) == 1, f"expected one inventory conflict; got {codes}"
