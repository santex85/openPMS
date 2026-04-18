"""GET /inventory/availability — coverage for availability_service."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.services.availability_service import (
    _room_counts_by_type,
    get_availability_grid,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL")
    return url


async def _with_fresh_engine(coro):
    """Run ``coro(engine)`` on a disposable async engine (avoids asyncio loop clashes)."""
    url = _database_url()
    eng = create_async_engine(url)
    try:
        await coro(eng)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_room_counts_by_type_empty_ids(db_engine: object) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            out = await _room_counts_by_type(session, uuid4(), [])
    assert out == {}


@pytest.mark.asyncio
async def test_room_counts_by_type_non_empty_counts_physical_rooms() -> None:
    """Covers the SQL aggregate branch (lines 23–33); HTTP TestClient often skips coverage."""
    from app.core.security import hash_password
    from app.models.auth.user import User
    from app.models.core.room import Room
    from app.models.core.tenant import Tenant
    from app.models.rates.rate import Rate
    from app.models.rates.rate_plan import RatePlan

    url = _database_url()
    eng = create_async_engine(url)
    try:
        tid = uuid4()
        owner_id = uuid4()
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                session.add(
                    Tenant(
                        id=tid,
                        name="CountTenant",
                        billing_email="ct@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tid,
                        email="o@count.example.com",
                        password_hash=hash_password("secret"),
                        full_name="O",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tid,
                    name="Count Prop",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                rt = RoomType(
                    tenant_id=tid,
                    property_id=prop.id,
                    name="Std",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(rt)
                await session.flush()
                rp = RatePlan(
                    tenant_id=tid,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rp)
                await session.flush()
                session.add(
                    Rate(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=date(2026, 10, 1),
                        price=10,
                    ),
                )
                session.add(
                    Room(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        name="201",
                        status="available",
                        housekeeping_status="clean",
                        housekeeping_priority="normal",
                    ),
                )
                session.add(
                    Room(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        name="202",
                        status="available",
                        housekeeping_status="clean",
                        housekeeping_priority="normal",
                    ),
                )
                rt_id = rt.id

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                counts = await _room_counts_by_type(session, tid, [rt_id])
        assert counts.get(rt_id) == 2
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_get_availability_grid_direct_covers_service() -> None:
    """Direct await so coverage traces get_availability_grid (async + TestClient gap)."""
    from app.core.security import hash_password
    from app.models.auth.user import User
    from app.models.core.room import Room
    from app.models.core.tenant import Tenant
    from app.models.rates.rate_plan import RatePlan

    url = _database_url()
    eng = create_async_engine(url)
    try:
        tid = uuid4()
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                session.add(
                    Tenant(
                        id=tid,
                        name="GridSvcTenant",
                        billing_email="gst@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=uuid4(),
                        tenant_id=tid,
                        email="u@gst.example.com",
                        password_hash=hash_password("secret"),
                        full_name="U",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tid,
                    name="Grid Prop",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                rt = RoomType(
                    tenant_id=tid,
                    property_id=prop.id,
                    name="King",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(rt)
                await session.flush()
                rp = RatePlan(
                    tenant_id=tid,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rp)
                await session.flush()
                session.add(
                    Room(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        name="301",
                        status="available",
                        housekeeping_status="clean",
                        housekeeping_priority="normal",
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        date=date(2026, 11, 1),
                        total_rooms=10,
                        booked_rooms=3,
                        blocked_rooms=2,
                    ),
                )
                pid = prop.id
                rt_id = rt.id

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                grid = await get_availability_grid(
                    session,
                    tid,
                    property_id=pid,
                    start_date=date(2026, 11, 1),
                    end_date=date(2026, 11, 2),
                    room_type_id=None,
                )
        assert grid is not None
        assert len(grid.cells) == 2
        c0 = next(c for c in grid.cells if c.date == date(2026, 11, 1))
        assert c0.total_rooms == 10 and c0.booked_rooms == 3 and c0.blocked_rooms == 2
        c1 = next(c for c in grid.cells if c.date == date(2026, 11, 2))
        assert c1.total_rooms == 1 and c1.booked_rooms == 0

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                none_prop = await get_availability_grid(
                    session,
                    tid,
                    property_id=uuid4(),
                    start_date=date(2026, 11, 1),
                    end_date=date(2026, 11, 1),
                    room_type_id=None,
                )
        assert none_prop is None

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                empty_rt = await get_availability_grid(
                    session,
                    tid,
                    property_id=pid,
                    start_date=date(2026, 11, 5),
                    end_date=date(2026, 11, 5),
                    room_type_id=rt_id,
                )
        assert empty_rt is not None and len(empty_rt.cells) == 1

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                bare = Property(
                    tenant_id=tid,
                    name="Bare Inn",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(bare)
                await session.flush()
                bare_pid = bare.id

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                no_types = await get_availability_grid(
                    session,
                    tid,
                    property_id=bare_pid,
                    start_date=date(2026, 12, 1),
                    end_date=date(2026, 12, 2),
                    room_type_id=None,
                )
        assert no_types is not None and no_types.cells == []
    finally:
        await eng.dispose()


def test_availability_grid_happy_path_with_ledger(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    pid = smoke_scenario["property_id"]
    rt_id = smoke_scenario["room_type_id"]
    owner_id = smoke_scenario["owner_id"]
    nights = (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3))

    async def _seed_ledger(eng: object) -> None:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                for d in nights:
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tid,
                            room_type_id=rt_id,
                            date=d,
                            total_rooms=5,
                            booked_rooms=2,
                            blocked_rooms=1,
                        ),
                    )

    asyncio.run(_with_fresh_engine(_seed_ledger))

    r = client.get(
        "/inventory/availability",
        params={
            "property_id": str(pid),
            "start_date": "2026-06-01",
            "end_date": "2026-06-03",
        },
        headers=auth_headers(tid, user_id=owner_id, role="owner"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["property_id"] == str(pid)
    cells = data["cells"]
    assert len(cells) == 3
    for c in cells:
        assert c["total_rooms"] == 5
        assert c["booked_rooms"] == 2
        assert c["blocked_rooms"] == 1
        assert c["available_rooms"] == 2


def test_availability_grid_no_ledger_uses_physical_room_count(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    pid = smoke_scenario["property_id"]
    rt_id = smoke_scenario["room_type_id"]
    owner_id = smoke_scenario["owner_id"]

    r = client.get(
        "/inventory/availability",
        params={
            "property_id": str(pid),
            "start_date": "2026-07-10",
            "end_date": "2026-07-10",
        },
        headers=auth_headers(tid, user_id=owner_id, role="owner"),
    )
    assert r.status_code == 200, r.text
    cells = r.json()["cells"]
    assert len(cells) == 1
    c = cells[0]
    assert c["room_type_id"] == str(rt_id)
    assert c["total_rooms"] == 1
    assert c["booked_rooms"] == 0
    assert c["blocked_rooms"] == 0
    assert c["available_rooms"] == 1


def test_availability_grid_no_room_types_empty_cells(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    owner_id = smoke_scenario["owner_id"]
    empty_holder: dict[str, UUID] = {}

    async def _seed_empty_prop(eng: object) -> None:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                prop = Property(
                    tenant_id=tid,
                    name="No Categories Hotel",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                empty_holder["id"] = prop.id

    asyncio.run(_with_fresh_engine(_seed_empty_prop))
    empty_pid = empty_holder["id"]

    r = client.get(
        "/inventory/availability",
        params={
            "property_id": str(empty_pid),
            "start_date": "2026-08-01",
            "end_date": "2026-08-02",
        },
        headers=auth_headers(tid, user_id=owner_id, role="owner"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["cells"] == []


def test_availability_grid_room_type_filter(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    pid = smoke_scenario["property_id"]
    std_rt = smoke_scenario["room_type_id"]
    owner_id = smoke_scenario["owner_id"]
    suite_holder: dict[str, UUID] = {}

    async def _add_suite_type(eng: object) -> None:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                suite = RoomType(
                    tenant_id=tid,
                    property_id=pid,
                    name="Suite",
                    base_occupancy=2,
                    max_occupancy=4,
                )
                session.add(suite)
                await session.flush()
                suite_holder["id"] = suite.id

    asyncio.run(_with_fresh_engine(_add_suite_type))
    suite_id = suite_holder["id"]

    base_params = {
        "property_id": str(pid),
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
    }
    r_all = client.get(
        "/inventory/availability",
        params=base_params,
        headers=auth_headers(tid, user_id=owner_id, role="owner"),
    )
    assert r_all.status_code == 200
    # Standard + Suite, two inclusive nights -> 2 room types * 2 days
    assert len(r_all.json()["cells"]) == 4

    r_f = client.get(
        "/inventory/availability",
        params={**base_params, "room_type_id": str(std_rt)},
        headers=auth_headers(tid, user_id=owner_id, role="owner"),
    )
    assert r_f.status_code == 200
    cells = r_f.json()["cells"]
    assert len(cells) == 2
    assert all(c["room_type_id"] == str(std_rt) for c in cells)
    assert not any(c["room_type_id"] == str(suite_id) for c in cells)
