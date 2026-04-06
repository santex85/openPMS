"""Availability overrides: ledger seeding and capacity rules."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def inventory_override_scenario(db_engine: object) -> dict[str, UUID]:
    """Two-night ledger fully seeded; room_type suitable for override API."""

    async def _seed() -> dict[str, UUID]:
        tenant_id = uuid4()
        owner_id = uuid4()
        nights = (date(2026, 9, 1), date(2026, 9, 2))
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="InvOverrideTenant",
                        billing_email="inv@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="owner.inv@example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Inv Prop",
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
                rate_plan = RatePlan(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rate_plan)
                await session.flush()
                for night in nights:
                    session.add(
                        Rate(
                            tenant_id=tenant_id,
                            room_type_id=room_type.id,
                            rate_plan_id=rate_plan.id,
                            date=night,
                            price=Decimal("80.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tenant_id,
                            room_type_id=room_type.id,
                            date=night,
                            total_rooms=5,
                            booked_rooms=1,
                            blocked_rooms=0,
                        ),
                    )

        return {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "room_type_id": room_type.id,
            "property_id": prop.id,
        }

    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed())


def test_availability_override_success(
    client,
    inventory_override_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = inventory_override_scenario["tenant_id"]
    oid = inventory_override_scenario["owner_id"]
    rt_id = inventory_override_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.put(
        "/inventory/availability/overrides",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
            "blocked_rooms": 2,
        },
    )
    assert r.status_code == 200
    assert r.json()["dates_updated"] == 2


def test_availability_override_ledger_gap_returns_422(
    client,
    inventory_override_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = inventory_override_scenario["tenant_id"]
    oid = inventory_override_scenario["owner_id"]
    rt_id = inventory_override_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.put(
        "/inventory/availability/overrides",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "blocked_rooms": 1,
        },
    )
    assert r.status_code == 422
    assert "ledger" in r.json()["detail"].lower()


def test_availability_override_exceeds_capacity_returns_409(
    client,
    inventory_override_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = inventory_override_scenario["tenant_id"]
    oid = inventory_override_scenario["owner_id"]
    rt_id = inventory_override_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.put(
        "/inventory/availability/overrides",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
            "blocked_rooms": 5,
        },
    )
    assert r.status_code == 409
    assert "blocked_rooms" in r.json()["detail"].lower()
