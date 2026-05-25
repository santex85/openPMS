"""POST / bookings + ledger: seeded rows, collisions, cancellations (TZ-19)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.rates.rate_plan import RatePlan

from tests.booking_seed import database_url
from tests.db_seed import disable_row_security_for_test_seed


def _url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def ledger_block_then_unblock(db_engine: object) -> dict[str, UUID]:
    """One room inventory: fully blocked; owner + receptionist."""

    async def _seed() -> dict[str, UUID]:
        tenant_id = uuid4()
        owner_id = uuid4()
        recv_id = uuid4()
        n1 = date(2028, 2, 1)
        n2 = date(2028, 2, 2)
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="Tz19 LedgerTenant",
                        billing_email="ldg@tz19.example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tenant_id,
                    name="Ledger Prop",
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
                    name="SoldOut",
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
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email=f"owner{owner_id.hex[:8]}@ldg.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                session.add(
                    User(
                        id=recv_id,
                        tenant_id=tenant_id,
                        email=f"recv{recv_id.hex[:8]}@ldg.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Rec",
                        role="receptionist",
                    ),
                )
                for n in (n1, n2):
                    session.add(
                        Rate(
                            tenant_id=tenant_id,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=n,
                            price=Decimal("40.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tenant_id,
                            room_type_id=rt.id,
                            date=n,
                            total_rooms=1,
                            booked_rooms=0,
                            blocked_rooms=1,
                        ),
                    )
        return {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "recv_id": recv_id,
            "room_type_id": rt.id,
            "rate_plan_id": rp.id,
            "property_id": prop.id,
            "night1": n1.isoformat(),
            "night2": n2.isoformat(),
        }

    if not _url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed())


def test_post_requires_ledger_row_per_night(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    if not database_url():
        pytest.skip("DATABASE_URL required")

    async def _seed_gap() -> dict[str, UUID]:
        tid = uuid4()
        uid = uuid4()
        n1 = date(2029, 1, 10)
        n2 = date(2029, 1, 11)
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                session.add(
                    Tenant(
                        id=tid,
                        name="GapLt",
                        billing_email="g@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tid,
                    name="P",
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
                    User(
                        id=uid,
                        tenant_id=tid,
                        email=f"g{uid.hex[:8]}@example.com",
                        password_hash=hash_password("secret"),
                        full_name="R",
                        role="receptionist",
                    ),
                )
                for nd in (n1, n2):
                    session.add(
                        Rate(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=nd,
                            price=Decimal("10.00"),
                        ),
                    )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tid,
                        room_type_id=rt.id,
                        date=n1,
                        total_rooms=5,
                        booked_rooms=0,
                        blocked_rooms=0,
                    ),
                )
        return {
            "tenant_id": tid,
            "user_id": uid,
            "pid": prop.id,
            "rt_id": rt.id,
            "rpid": rp.id,
            "n2": n2,
        }

    ctx = asyncio.run(_seed_gap())
    h = auth_headers(ctx["tenant_id"], user_id=ctx["user_id"], role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["pid"]),
            "room_type_id": str(ctx["rt_id"]),
            "rate_plan_id": str(ctx["rpid"]),
            "check_in": "2029-01-10",
            "check_out": "2029-01-12",
            "guest": {
                "first_name": "A",
                "last_name": "B",
                "email": "gap@example.com",
                "phone": "+111",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert r.status_code == 422
    assert "ledger" in str(r.json()["detail"]).lower()


def _run_sum_booked(
    *,
    tenant_id: UUID,
    rt_id: UUID,
    nights: list[date],
) -> int:
    url = database_url()
    assert url

    async def _go() -> int:
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tenant_id)},
                )
                row = (
                    (
                        await session.execute(
                            select(func.coalesce(func.sum(AvailabilityLedger.booked_rooms), 0)).where(
                                AvailabilityLedger.tenant_id == tenant_id,
                                AvailabilityLedger.room_type_id == rt_id,
                                AvailabilityLedger.date.in_(nights),
                            ),
                        )
                    )
                ).scalar_one()
            return int(row)
        finally:
            await engine.dispose()

    return asyncio.run(_go())


@pytest.fixture
def ledger_full_fixture(db_engine: object) -> dict[str, UUID]:
    """two nights total_rooms 1 booked_rooms 1."""

    async def _s() -> dict[str, UUID]:
        tid = uuid4()
        uid = uuid4()
        n1 = date(2029, 2, 1)
        n2 = date(2029, 2, 2)
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                session.add(
                    Tenant(
                        id=tid,
                        name="FulLt",
                        billing_email="f@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tid,
                    name="P2",
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
                    User(
                        id=uid,
                        tenant_id=tid,
                        email=f"f{uid.hex[:8]}@example.com",
                        password_hash=hash_password("secret"),
                        full_name="R",
                        role="receptionist",
                    ),
                )
                for n in (n1, n2):
                    session.add(
                        Rate(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=n,
                            price=Decimal("44.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            date=n,
                            total_rooms=1,
                            booked_rooms=1,
                            blocked_rooms=0,
                        ),
                    )
        return {
            "tenant_id": tid,
            "user_id": uid,
            "pid": prop.id,
            "rt_id": rt.id,
            "rpid": rp.id,
        }

    if not _url():
        pytest.skip("DATABASE_URL required")
    ctx = asyncio.run(_s())
    ctx["factory"] = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ctx["nights"] = [date(2029, 2, 1), date(2029, 2, 2)]
    return ctx


def test_post_insufficient_inventory_fully_booked(
    client,
    ledger_full_fixture: dict,
    auth_headers,
) -> None:
    lf = ledger_full_fixture
    h = auth_headers(lf["tenant_id"], user_id=lf["user_id"], role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(lf["pid"]),
            "room_type_id": str(lf["rt_id"]),
            "rate_plan_id": str(lf["rpid"]),
            "check_in": "2029-02-01",
            "check_out": "2029-02-03",
            "guest": {
                "first_name": "X",
                "last_name": "Y",
                "email": "fullbk@example.com",
                "phone": "+222",
            },
            "force_new_guest": True,
            "status": "confirmed",
        },
    )
    assert r.status_code == 409
    assert "inventory" in str(r.json()["detail"]).lower()


@pytest.fixture
def ledger_blocked_fixture(db_engine: object) -> dict[str, UUID]:
    """two nights total 2 booked 0 blocked 2."""

    async def _s() -> dict[str, UUID]:
        tid = uuid4()
        uid = uuid4()
        n1 = date(2029, 3, 1)
        n2 = date(2029, 3, 2)
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                session.add(
                    Tenant(
                        id=tid,
                        name="BlkLt",
                        billing_email="b@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tid,
                    name="Pb",
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
                    User(
                        id=uid,
                        tenant_id=tid,
                        email=f"b{uid.hex[:8]}@example.com",
                        password_hash=hash_password("secret"),
                        full_name="R",
                        role="receptionist",
                    ),
                )
                for n in (n1, n2):
                    session.add(
                        Rate(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=n,
                            price=Decimal("15.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            date=n,
                            total_rooms=2,
                            booked_rooms=0,
                            blocked_rooms=2,
                        ),
                    )
        return {
            "tenant_id": tid,
            "user_id": uid,
            "pid": prop.id,
            "rt_id": rt.id,
            "rpid": rp.id,
        }

    if not _url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_s())


def test_post_insufficient_inventory_all_blocked(
    client,
    ledger_blocked_fixture: dict,
    auth_headers,
) -> None:
    lf = ledger_blocked_fixture
    h = auth_headers(lf["tenant_id"], user_id=lf["user_id"], role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(lf["pid"]),
            "room_type_id": str(lf["rt_id"]),
            "rate_plan_id": str(lf["rpid"]),
            "check_in": "2029-03-01",
            "check_out": "2029-03-03",
            "guest": {
                "first_name": "B",
                "last_name": "K",
                "email": "blockall@example.com",
                "phone": "+223",
            },
            "force_new_guest": True,
            "status": "confirmed",
        },
    )
    assert r.status_code == 409


def test_blocked_until_override_then_post_succeeds(
    client,
    ledger_block_then_unblock: dict[str, UUID],
    auth_headers,
    auth_headers_user,
) -> None:
    d = ledger_block_then_unblock
    tid = d["tenant_id"]
    h_put = auth_headers_user(tid, d["owner_id"], role="owner")
    hr = auth_headers_user(tid, d["recv_id"], role="receptionist")
    r0 = client.post(
        "/bookings",
        headers=hr,
        json={
            "property_id": str(d["property_id"]),
            "room_type_id": str(d["room_type_id"]),
            "rate_plan_id": str(d["rate_plan_id"]),
            "check_in": d["night1"],
            "check_out": "2028-02-03",
            "guest": {
                "first_name": "N",
                "last_name": "O",
                "email": "noavail@blocked.example.com",
                "phone": "+999",
            },
            "force_new_guest": True,
            "status": "confirmed",
        },
    )
    assert r0.status_code == 409

    pu = client.put(
        "/inventory/availability/overrides",
        headers=h_put,
        json={
            "room_type_id": str(d["room_type_id"]),
            "start_date": d["night1"],
            "end_date": d["night2"],
            "blocked_rooms": 0,
        },
    )
    assert pu.status_code == 200

    r_ok = client.post(
        "/bookings",
        headers=hr,
        json={
            "property_id": str(d["property_id"]),
            "room_type_id": str(d["room_type_id"]),
            "rate_plan_id": str(d["rate_plan_id"]),
            "check_in": d["night1"],
            "check_out": "2028-02-03",
            "guest": {
                "first_name": "Ok",
                "last_name": "Now",
                "email": "oknow@blocked.example.com",
                "phone": "+888",
            },
            "force_new_guest": True,
            "status": "confirmed",
        },
    )
    assert r_ok.status_code == 201


def test_patch_cancel_returns_inventory(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]

    nights = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]

    async def _resolve_rt_and_before() -> tuple[UUID, int]:
        url = database_url()
        assert url
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            async def _inner() -> tuple[UUID, int]:
                async with factory() as session:
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                        ),
                        {"tid": str(tid)},
                    )
                    stmt = (
                        select(AvailabilityLedger.room_type_id)
                        .where(
                            AvailabilityLedger.tenant_id == tid,
                            AvailabilityLedger.date.in_(nights[:1]),
                        )
                        .limit(1)
                    )
                    rt_id = (await session.execute(stmt)).scalar_one()
                    b0 = (
                        await session.execute(
                            select(
                                func.coalesce(
                                    func.sum(AvailabilityLedger.booked_rooms),
                                    0,
                                ),
                            ).where(
                                AvailabilityLedger.tenant_id == tid,
                                AvailabilityLedger.room_type_id == rt_id,
                                AvailabilityLedger.date.in_(nights),
                            ),
                        )
                    ).scalar_one()
                return rt_id, int(b0)

            return await _inner()
        finally:
            await engine.dispose()

    rt_id, before = asyncio.run(_resolve_rt_and_before())
    assert before >= 1

    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "cancelled"})
    assert r.status_code == 204

    after = _run_sum_booked(tenant_id=tid, rt_id=rt_id, nights=nights)
    assert after == before - 3



def test_patch_no_show_returns_inventory(
    client,
    auth_headers,
) -> None:
    """no_show releases inventory for each booking night (two nights → sum −2)."""
    if not _url():
        pytest.skip()

    async def _seed() -> dict[str, UUID]:
        tid = uuid4()
        uid = uuid4()
        nights_l = [date(2035, 4, 1), date(2035, 4, 2)]
        url = database_url()
        assert url
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            bid_out: UUID
            rt_out: UUID
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
                            name="NsLt",
                            billing_email="ns@example.com",
                            status="active",
                        ),
                    )
                    await session.flush()
                    prop = Property(
                        tenant_id=tid,
                        name="Pn",
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
                        User(
                            id=uid,
                            tenant_id=tid,
                            email=f"ns{uid.hex[:8]}@example.com",
                            password_hash=hash_password("secret"),
                            full_name="R",
                            role="receptionist",
                        ),
                    )
                    g = Guest(
                        tenant_id=tid,
                        first_name="N",
                        last_name="S",
                        email="nsgo@example.com",
                        phone="+1",
                    )
                    session.add(g)
                    await session.flush()
                    for n in nights_l:
                        session.add(
                            Rate(
                                tenant_id=tid,
                                room_type_id=rt.id,
                                rate_plan_id=rp.id,
                                date=n,
                                price=Decimal("55.00"),
                            ),
                        )
                        session.add(
                            AvailabilityLedger(
                                tenant_id=tid,
                                room_type_id=rt.id,
                                date=n,
                                total_rooms=10,
                                booked_rooms=1,
                                blocked_rooms=0,
                            ),
                        )
                    bk = Booking(
                        tenant_id=tid,
                        property_id=prop.id,
                        guest_id=g.id,
                        rate_plan_id=rp.id,
                        status="confirmed",
                        source="test",
                        total_amount=Decimal("110.00"),
                    )
                    session.add(bk)
                    await session.flush()
                    bid_out = bk.id
                    rt_out = rt.id
                    for n in nights_l:
                        session.add(
                            BookingLine(
                                tenant_id=tid,
                                booking_id=bid_out,
                                date=n,
                                room_type_id=rt.id,
                                room_id=None,
                                price_for_date=Decimal("55.00"),
                            ),
                        )
            return {
                "tenant_id": tid,
                "user_id": uid,
                "booking_id": bid_out,
                "room_type_id": rt_out,
                "nights": nights_l,
            }
        finally:
            await engine.dispose()

    sc = asyncio.run(_seed())
    before = _run_sum_booked(
        tenant_id=sc["tenant_id"],
        rt_id=sc["room_type_id"],
        nights=list(sc["nights"]),
    )
    h = auth_headers(sc["tenant_id"], user_id=sc["user_id"], role="receptionist")
    r = client.patch(
        f'/bookings/{sc["booking_id"]}',
        headers=h,
        json={"status": "no_show"},
    )
    assert r.status_code == 204
    after = _run_sum_booked(
        tenant_id=sc["tenant_id"],
        rt_id=sc["room_type_id"],
        nights=list(sc["nights"]),
    )
    assert after == before - 2
