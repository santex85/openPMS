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
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.services.availability_ledger_seed import seed_empty_availability_ledger_year_forward
from app.services.room_type_service import count_rooms_for_room_type


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
    parser = argparse.ArgumentParser(description="Concurrent booking load test")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Running OpenPMS API base URL",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="Number of parallel POST /bookings",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
