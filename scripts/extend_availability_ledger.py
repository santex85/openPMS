#!/usr/bin/env python3
"""
Extend ``availability_ledger`` beyond the current MAX(date) for each room type, or seed
``extra_days`` when a type has no rows (same empty pattern as initial seed).

**RLS:** sets ``app.tenant_id`` per tenant. If ``TENANT_ID`` is unset, loads all tenant ids
with ``SELECT id FROM tenants`` (requires a DB role that can see all tenants, e.g. the default
``openpms`` superuser from the Postgres Docker image). To extend a single tenant, set
``TENANT_ID=<uuid>``.

**Cron (monthly example):**

  docker compose run --rm -e PYTHONPATH=/app api python scripts/extend_availability_ledger.py

Optional: ``--days N`` (default 30).

Requires ``DATABASE_URL`` in the environment (as with the API container).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.availability_ledger_seed import extend_availability_ledger_days


async def _tenant_ids(session: AsyncSession) -> list[UUID]:
    res = await session.execute(text("SELECT id FROM tenants ORDER BY id"))
    return [row[0] for row in res.fetchall()]


async def _run_for_tenant(session: AsyncSession, tenant_id: UUID, extra_days: int) -> int:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )
    return await extend_availability_ledger_days(session, extra_days=extra_days)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Extend availability_ledger horizon.")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days to append after MAX(date), or horizon when ledger is empty (default: 30)",
    )
    args = parser.parse_args()
    extra_days = args.days

    try:
        database_url = os.environ["DATABASE_URL"]
    except KeyError as exc:
        print(f"Missing environment variable: {exc}", file=sys.stderr)
        return 1

    if extra_days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 1

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            async with session.begin():
                tid_env = os.environ.get("TENANT_ID")
                if tid_env:
                    tenant_id = UUID(tid_env.strip())
                    total = await _run_for_tenant(session, tenant_id, extra_days)
                else:
                    tenants = await _tenant_ids(session)
                    if not tenants:
                        print(
                            "No tenants found. For non-superuser roles set TENANT_ID "
                            "or use a connection that can read all tenants.",
                            file=sys.stderr,
                        )
                        return 2
                    total = 0
                    for tid in tenants:
                        total += await _run_for_tenant(session, tid, extra_days)
        print(f"Inserted or seeded {total} availability_ledger row(s) (idempotent skips OK).")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
