#!/usr/bin/env python3
"""
Task 5.2: fire 100 concurrent POST /bookings for the last available villa (one room night).

Expect exactly one HTTP 201 and ninety-nine HTTP 409 responses when the API and DB use
row-level locking on availability_ledger (SELECT ... FOR UPDATE).

Prerequisites: migrated Postgres, API running. ``JWT_SECRET`` must match the API process exactly
(see ``docker compose exec api printenv JWT_SECRET`` if unsure). ``DATABASE_URL`` must reach the
same Postgres the API uses.

Docker Compose (script in a one-off container on the project network):

  docker compose up -d api
  docker compose run --rm -e PYTHONPATH=/app \\
    -e DATABASE_URL=postgresql+asyncpg://openpms:openpms@db:5432/openpms \\
    -e JWT_SECRET=\"<same as api>\" \\
    api python scripts/load_test_overbooking.py --base-url http://api:8000

Local API + local script:

  export DATABASE_URL=postgresql+asyncpg://...
  export JWT_SECRET=...
  PYTHONPATH=. python scripts/load_test_overbooking.py --base-url http://127.0.0.1:8000

Optional: ``--concurrency`` (default 100).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, time, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import jwt
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.services.availability_ledger_seed import seed_empty_availability_ledger_year_forward
from app.services.room_type_service import count_rooms_for_room_type



from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.room import Room


async def _seed_two_rooms_two_bookings(*, database_url: str, tenant_id) -> dict:
    """Two physical rooms same type; two confirmed one-night bookings (unassigned)."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    check_in = date.today() + timedelta(days=40)
    check_out = check_in + timedelta(days=1)
    prop_id = rt_id = rp_id = None
    r1_id = r2_id = None
    b1_id = b2_id = None
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="RoomAssignTenant",
                    billing_email="ra@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="RA Resort",
                timezone="UTC",
                currency="USD",
                checkin_time=time(15, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Twin",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            for name in ("101", "102"):
                session.add(
                    Room(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        name=name,
                        status="available",
                    ),
                )
            await session.flush()
            rooms = (
                await session.execute(
                    select(Room.id).where(
                        Room.tenant_id == tenant_id,
                        Room.room_type_id == rt.id,
                    ).order_by(Room.name)
                )
            ).all()
            r1_id, r2_id = rooms[0][0], rooms[1][0]
            total_rooms = await count_rooms_for_room_type(session, tenant_id, rt.id)
            await seed_empty_availability_ledger_year_forward(
                session,
                tenant_id=tenant_id,
                room_type_id=rt.id,
                total_rooms=total_rooms,
                start_date=date.today(),
            )
            await session.execute(
                update(AvailabilityLedger)
                .where(
                    AvailabilityLedger.tenant_id == tenant_id,
                    AvailabilityLedger.room_type_id == rt.id,
                )
                .values(total_rooms=total_rooms),
            )
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="Flexible",
            )
            session.add(rp)
            await session.flush()
            session.add(
                Rate(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=check_in,
                    price=Decimal("100.00"),
                ),
            )
            prop_id = prop.id
            rt_id = rt.id
            rp_id = rp.id
            for idx in (1, 2):
                g = Guest(
                    tenant_id=tenant_id,
                    first_name="G",
                    last_name=str(idx),
                    email=f"guest{idx}@ra.example.com",
                    phone=f"+1000000000{idx}",
                )
                session.add(g)
                await session.flush()
                bk = Booking(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=g.id,
                    rate_plan_id=rp.id,
                    status="confirmed",
                    source="load_test_ra",
                    total_amount=Decimal("100.00"),
                )
                session.add(bk)
                await session.flush()
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=bk.id,
                        date=check_in,
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("100.00"),
                    ),
                )
                if idx == 1:
                    b1_id = bk.id
                else:
                    b2_id = bk.id
    await engine.dispose()
    return {
        "tenant_id": str(tenant_id),
        "property_id": str(prop_id),
        "room_type_id": str(rt_id),
        "rate_plan_id": str(rp_id),
        "room1_id": str(r1_id),
        "room2_id": str(r2_id),
        "booking1_id": str(b1_id),
        "booking2_id": str(b2_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    }


async def _run_room_assign_conflict(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL")
    jwt_secret = os.environ.get("JWT_SECRET")
    if not database_url or not jwt_secret:
        print("DATABASE_URL and JWT_SECRET must be set.", file=sys.stderr)
        return 1
    tenant_id = uuid4()
    seed = await _seed_two_rooms_two_bookings(database_url=database_url, tenant_id=tenant_id)
    user_sub = uuid4()
    token = jwt.encode(
        {
            "tenant_id": seed["tenant_id"],
            "sub": str(user_sub),
            "role": "receptionist",
        },
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    base = args.base_url.rstrip("/")
    room1 = seed["room1_id"]
    b1 = seed["booking1_id"]
    b2 = seed["booking2_id"]

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        ra, rb = await asyncio.gather(
            client.patch(
                f"{base}/bookings/{b1}",
                json={"room_id": room1},
                headers=headers,
            ),
            client.patch(
                f"{base}/bookings/{b2}",
                json={"room_id": room1},
                headers=headers,
            ),
        )
    codes = {ra.status_code, rb.status_code}
    print("Concurrent PATCH same room:", ra.status_code, rb.status_code)
    if codes != {204, 409}:
        print("Expected one 204 and one 409 (room double-book guard).", file=sys.stderr)
        return 2
    print("Room assignment conflict guard: OK.")
    return 0

async def _seed_single_villa_scenario(
    *,
    database_url: str,
    tenant_id,
) -> dict:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=1)

    prop_id = rt_id = rp_id = None
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="LoadTestTenant",
                    billing_email="load@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Villa Resort",
                timezone="UTC",
                currency="USD",
                checkin_time=time(15, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Single Villa",
                base_occupancy=2,
                max_occupancy=4,
            )
            session.add(rt)
            await session.flush()
            total_rooms = await count_rooms_for_room_type(session, tenant_id, rt.id)
            await seed_empty_availability_ledger_year_forward(
                session,
                tenant_id=tenant_id,
                room_type_id=rt.id,
                total_rooms=total_rooms,
            )
            await session.execute(
                update(AvailabilityLedger)
                .where(
                    AvailabilityLedger.tenant_id == tenant_id,
                    AvailabilityLedger.room_type_id == rt.id,
                )
                .values(total_rooms=1),
            )
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="Non-refundable",
            )
            session.add(rp)
            await session.flush()
            session.add(
                Rate(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=check_in,
                    price=Decimal("999.00"),
                ),
            )
            prop_id = prop.id
            rt_id = rt.id
            rp_id = rp.id

    await engine.dispose()

    return {
        "tenant_id": str(tenant_id),
        "property_id": str(prop_id),
        "room_type_id": str(rt_id),
        "rate_plan_id": str(rp_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    }


def _build_payload(seed: dict) -> dict:
    return {
        "property_id": seed["property_id"],
        "room_type_id": seed["room_type_id"],
        "rate_plan_id": seed["rate_plan_id"],
        "check_in": seed["check_in"],
        "check_out": seed["check_out"],
        "guest": {
            "first_name": "Load",
            "last_name": "Test",
            "email": "guest@example.com",
            "phone": "+19999999999",
        },
        "status": "confirmed",
        "source": "load_test",
    }


async def _run(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL")
    jwt_secret = os.environ.get("JWT_SECRET")
    if not database_url or not jwt_secret:
        print("DATABASE_URL and JWT_SECRET must be set.", file=sys.stderr)
        return 1

    tenant_id = uuid4()
    seed = await _seed_single_villa_scenario(database_url=database_url, tenant_id=tenant_id)
    token = jwt.encode(
        {"tenant_id": seed["tenant_id"]},
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    payload = _build_payload(seed)
    url = f"{args.base_url.rstrip('/')}/bookings"

    async def one(client: httpx.AsyncClient) -> int:
        r = await client.post(url, json=payload, headers=headers)
        return r.status_code

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        tasks = [one(client) for _ in range(args.concurrency)]
        statuses = await asyncio.gather(*tasks)

    from collections import Counter

    counts = Counter(statuses)
    print("Status code counts:", dict(counts))
    ok = counts.get(201, 0) == 1 and counts.get(409, 0) == args.concurrency - 1
    if not ok:
        print(
            f"Expected 1x 201 and {args.concurrency - 1}x 409; got {dict(counts)}",
            file=sys.stderr,
        )
        return 2
    print("Overbooking guard: OK (1 success, 99 conflicts).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent booking load tests")
    parser.add_argument(
        "--scenario",
        choices=("overbooking", "room-assign"),
        default="overbooking",
        help="overbooking: N POST /bookings for last room night; "
        "room-assign: two PATCH assign same physical room",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Running OpenPMS API base URL",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="Number of parallel POST /bookings (overbooking only)",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.scenario == "room-assign":
        raise SystemExit(asyncio.run(_run_room_assign_conflict(args)))
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
