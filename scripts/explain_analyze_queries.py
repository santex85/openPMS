#!/usr/bin/env python3
"""
Run EXPLAIN (ANALYZE, BUFFERS) on hot paths for booking list and room-conflict checks.

Requires DATABASE_URL (asyncpg URL) and applied migrations.

Usage:
  export DATABASE_URL=postgresql+asyncpg://openpms:openpms@127.0.0.1:5432/openpms
  PYTHONPATH=. python scripts/explain_analyze_queries.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL required", file=sys.stderr)
        return 1
    tenant_id = uuid4()
    property_id = uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            sql_list = text("""
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT b.id FROM bookings b
                WHERE b.tenant_id = CAST(:tenant_id AS uuid)
                  AND b.property_id = CAST(:property_id AS uuid)
                  AND EXISTS (
                    SELECT 1 FROM booking_lines bl
                    WHERE bl.booking_id = b.id AND bl.tenant_id = b.tenant_id
                      AND bl.date >= :start_date AND bl.date <= :end_date
                  )
                ORDER BY b.id
                LIMIT 50
            """)
            res = await session.execute(
                sql_list,
                {
                    "tenant_id": str(tenant_id),
                    "property_id": str(property_id),
                    "start_date": date(2026, 3, 1),
                    "end_date": date(2026, 3, 31),
                },
            )
            print("--- list_bookings-style EXISTS ---")
            for row in res.all():
                print(row[0])

            room_id = uuid4()
            exclude = uuid4()
            night = date(2026, 6, 1)
            sql_conflict = text("""
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT bl.id FROM booking_lines bl
                JOIN bookings b ON b.tenant_id = bl.tenant_id AND b.id = bl.booking_id
                WHERE bl.tenant_id = CAST(:tenant_id AS uuid)
                  AND bl.room_id = CAST(:room_id AS uuid)
                  AND bl.date = :night
                  AND bl.booking_id != CAST(:exclude AS uuid)
                  AND b.status NOT IN ('cancelled', 'no_show')
                LIMIT 1
            """)
            print("--- room conflict probe ---")
            res2 = await session.execute(
                sql_conflict,
                {
                    "tenant_id": str(tenant_id),
                    "room_id": str(room_id),
                    "exclude": str(exclude),
                    "night": night,
                },
            )
            for row in res2.all():
                print(row[0])

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
