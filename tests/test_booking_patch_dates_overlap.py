"""PATCH booking: date changes where old and new stay windows overlap."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.booking_seed import database_url, seed_rate_span
from tests.db_seed import disable_row_security_for_test_seed


def test_patch_extend_stay_overlapping_nights(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    """3 nights -> 4 nights, same overlapping core; inventory single room."""

    ctx = asyncio.run(_seed_overlap_env(db_engine))
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pid = ctx["property_id"]
    rt = ctx["room_type_id"]
    rp = ctx["rate_plan_id"]

    payload = _post_payload(pid, rt, rp, "2026-04-01", "2026-04-04")
    cr = client.post("/bookings", headers=h, json=payload)
    assert cr.status_code == 201, cr.text
    bid = UUID(cr.json()["booking_id"])

    patch = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_in": "2026-04-01", "check_out": "2026-04-06"},
    )
    assert patch.status_code == 204
    g = client.get(f"/bookings/{bid}", headers=h).json()
    assert Decimal(str(g["total_amount"])) == Decimal("250.00")


def test_patch_shrink_stay_overlapping_nights(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    ctx = asyncio.run(_seed_overlap_env(db_engine))
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    payload = _post_payload(
        ctx["property_id"],
        ctx["room_type_id"],
        ctx["rate_plan_id"],
        "2026-04-01",
        "2026-04-05",
    )
    cr = client.post("/bookings", headers=h, json=payload)
    assert cr.status_code == 201
    bid = UUID(cr.json()["booking_id"])

    client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_in": "2026-04-02", "check_out": "2026-04-05"},
    )
    g = client.get(f"/bookings/{bid}", headers=h).json()
    assert Decimal(str(g["total_amount"])) == Decimal("150.00")


def test_patch_shift_stay_partial_overlap(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    ctx = asyncio.run(_seed_overlap_env(db_engine))
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    payload = _post_payload(
        ctx["property_id"],
        ctx["room_type_id"],
        ctx["rate_plan_id"],
        "2026-04-01",
        "2026-04-05",
    )
    cr = client.post("/bookings", headers=h, json=payload)
    assert cr.status_code == 201
    bid = UUID(cr.json()["booking_id"])

    client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_in": "2026-04-03", "check_out": "2026-04-07"},
    )
    g = client.get(f"/bookings/{bid}", headers=h).json()
    assert Decimal(str(g["total_amount"])) == Decimal("200.00")


def test_patch_dates_no_inventory_on_new_nights_returns_409(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    ctx = asyncio.run(_seed_overlap_env(db_engine))
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pid = ctx["property_id"]
    rt = ctx["room_type_id"]
    rp = ctx["rate_plan_id"]

    r1 = client.post(
        "/bookings",
        headers=h,
        json=_post_payload(pid, rt, rp, "2026-04-01", "2026-04-06"),
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/bookings",
        headers=h,
        json=_post_guest(
            pid,
            rt,
            rp,
            "2026-04-15",
            "2026-04-20",
            "other@test.example.com",
        ),
    )
    assert r2.status_code == 201

    bid_a = UUID(r1.json()["booking_id"])
    pr = client.patch(
        f"/bookings/{bid_a}",
        headers=h,
        json={"check_in": "2026-04-01", "check_out": "2026-04-26"},
    )
    assert pr.status_code == 409


def test_patch_dates_missing_rates_on_new_nights_returns_422(
    client,
    auth_headers,
    db_engine: object,
) -> None:
    ctx = asyncio.run(_seed_overlap_env(db_engine, include_late_rates=False))
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    payload = _post_payload(
        ctx["property_id"],
        ctx["room_type_id"],
        ctx["rate_plan_id"],
        "2026-04-01",
        "2026-04-03",
    )
    cr = client.post("/bookings", headers=h, json=payload)
    assert cr.status_code == 201
    bid = UUID(cr.json()["booking_id"])

    pr = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_in": "2026-04-15", "check_out": "2026-04-18"},
    )
    assert pr.status_code == 422


def _post_payload(
    pid: UUID | str,
    rt: UUID | str,
    rp: UUID | str,
    ci: str,
    co: str,
) -> dict:
    return _post_guest(pid, rt, rp, ci, co, "shift@test.example.com")


def _post_guest(
    pid: UUID | str,
    rt: UUID | str,
    rp: UUID | str,
    ci: str,
    co: str,
    email: str,
) -> dict:
    return {
        "property_id": str(pid),
        "room_type_id": str(rt),
        "rate_plan_id": str(rp),
        "check_in": ci,
        "check_out": co,
        "guest": {
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "phone": "+12125551212",
        },
        "force_new_guest": True,
        "status": "confirmed",
    }


async def _seed_overlap_env(
    db_engine: object,
    *,
    include_late_rates: bool = True,
) -> dict[str, object]:
    if not database_url():
        pytest.skip("DATABASE_URL required")

    url = database_url()
    assert url

    async def __inner() -> dict[str, object]:
        from app.core.security import hash_password
        from app.models.auth.user import User
        from app.models.bookings.guest import Guest
        from app.models.core.property import Property
        from app.models.core.room_type import RoomType
        from app.models.core.tenant import Tenant
        from app.models.rates.rate_plan import RatePlan

        tenant_id = uuid4()
        uid = uuid4()


        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        rtid_val: UUID | None = None

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
                        name="Overlap-T",
                        billing_email="o@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tenant_id,
                    name="O Prop",
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
                    name="S",
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
                        id=uid,
                        tenant_id=tenant_id,
                        email="rcv@overlap.example.com",
                        password_hash=hash_password("x"),
                        full_name="R",
                        role="receptionist",
                    ),
                )
                session.add(
                    Guest(
                        tenant_id=tenant_id,
                        first_name="G",
                        last_name="H",
                        email="preload@overlap.example.com",
                        phone="+1",
                    ),
                )
                num = 35 if include_late_rates else 14
                await seed_rate_span(
                    session,
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    start=date(2026, 4, 1),
                    num_days=num,
                    price=Decimal("50.00"),
                    total_rooms=1,
                )
                pid = prop.id
                rtid_val = rt.id
                prid = rp.id

        return {
            "tenant_id": tenant_id,
            "user_id": uid,
            "property_id": pid,
            "room_type_id": rtid_val,
            "rate_plan_id": prid,
        }

    return await __inner()

