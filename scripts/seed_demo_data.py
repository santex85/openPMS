#!/usr/bin/env python3
"""
Create demo tenant, property, room types, rooms, and availability ledger; print a JWT for /login.

Uses RLS session variable app.tenant_id like the API.

Usage (from repo root, with Postgres up):

  docker compose run --rm -e PYTHONPATH=/app api python scripts/seed_demo_data.py

Requires DATABASE_URL and JWT_SECRET in the environment (set by docker-compose for api).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.services.availability_ledger_seed import seed_empty_availability_ledger_year_forward


async def _set_tenant_rls(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )


def _mint_jwt(tenant_id: UUID, secret: str) -> str:
    payload = {
        "tenant_id": str(tenant_id),
        "sub": str(uuid4()),
        "exp": datetime.now(timezone.utc) + timedelta(days=365),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def main() -> int:
    try:
        database_url = os.environ["DATABASE_URL"]
        jwt_secret = os.environ["JWT_SECRET"]
    except KeyError as exc:
        print(f"Missing environment variable: {exc}", file=sys.stderr)
        return 1

    tenant_id = uuid4()
    property_id = uuid4()

    # name, room_count, base_occ, max_occ, room_name_prefix
    room_specs: list[tuple[str, int, int, int, str]] = [
        ("Pool Villa", 5, 2, 4, "PV"),
        ("Ocean View Suite", 10, 2, 3, "OVS"),
        ("Standard", 15, 2, 2, "STD"),
    ]

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            async with session.begin():
                await _set_tenant_rls(session, tenant_id)

                tenant = Tenant(
                    id=tenant_id,
                    name="Demo Hotel Group",
                    billing_email="demo@openpms.local",
                    status="active",
                )
                session.add(tenant)
                await session.flush()

                prop = Property(
                    id=property_id,
                    tenant_id=tenant_id,
                    name="Grand Samui Resort",
                    timezone="Asia/Bangkok",
                    currency="THB",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()

                for name, count, base_occ, max_occ, prefix in room_specs:
                    rt_id = uuid4()
                    rt = RoomType(
                        id=rt_id,
                        tenant_id=tenant_id,
                        property_id=property_id,
                        name=name,
                        base_occupancy=base_occ,
                        max_occupancy=max_occ,
                    )
                    session.add(rt)
                    await session.flush()

                    for i in range(1, count + 1):
                        room = Room(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            room_type_id=rt_id,
                            name=f"{prefix}-{i}",
                            status="available",
                        )
                        session.add(room)

                    await session.flush()
                    await seed_empty_availability_ledger_year_forward(
                        session,
                        tenant_id=tenant_id,
                        room_type_id=rt_id,
                        total_rooms=count,
                    )

        token = _mint_jwt(tenant_id, jwt_secret)
        print()
        print("--- Demo seed complete ---")
        print(f"tenant_id: {tenant_id}")
        print(f"property_id: {property_id}")
        print()
        print("Paste this Bearer token on the frontend /login page:")
        print()
        print(token)
        print()
        return 0

    except IntegrityError as exc:
        print(
            "Database constraint violation (likely demo data already exists). "
            "Use a fresh DB or remove existing rows.",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        return 2
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
