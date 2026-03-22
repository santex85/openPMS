#!/usr/bin/env python3
"""
Seed ``availability_ledger`` with 365 daily rows for one room type (same logic as POST /room-types).

Uses the same RLS session variable as the API. Fails if any ledger rows already exist for that
room type (avoid unique violations).

Usage (from repo root):

  PYTHONPATH=. DATABASE_URL=postgresql+asyncpg://... \\
    TENANT_ID=<uuid> ROOM_TYPE_ID=<uuid> \\
    python scripts/seed_availability_ledger_for_room_type.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.rates.availability_ledger import AvailabilityLedger
from app.services.availability_ledger_seed import seed_empty_availability_ledger_year_forward
from app.services.room_type_service import count_rooms_for_room_type


async def _existing_ledger_rows(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
) -> int:
    stmt = (
        select(func.count())
        .select_from(AvailabilityLedger)
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            AvailabilityLedger.room_type_id == room_type_id,
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def main() -> int:
    try:
        database_url = os.environ["DATABASE_URL"]
        tenant_id = UUID(os.environ["TENANT_ID"])
        room_type_id = UUID(os.environ["ROOM_TYPE_ID"])
    except (KeyError, ValueError) as exc:
        print("Missing or invalid DATABASE_URL, TENANT_ID, or ROOM_TYPE_ID", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            existing = await _existing_ledger_rows(session, tenant_id, room_type_id)
            if existing > 0:
                print(
                    f"Refusing to seed: {existing} availability_ledger row(s) already exist "
                    f"for this room type.",
                    file=sys.stderr,
                )
                return 2
            total_rooms = await count_rooms_for_room_type(session, tenant_id, room_type_id)
            await seed_empty_availability_ledger_year_forward(
                session,
                tenant_id=tenant_id,
                room_type_id=room_type_id,
                total_rooms=total_rooms,
            )
    await engine.dispose()
    print("Seeded 365-day availability ledger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
