#!/usr/bin/env python3
"""
Delete refresh token rows that are revoked or past ``expires_at``.

Uses the same tenant-scoped RLS pattern as other maintenance scripts: when ``TENANT_ID``
is unset, iterates all tenants (requires a DB role that can ``SELECT id FROM tenants``,
e.g. the ``openpms`` superuser from the Postgres Docker image).

**Cron (weekly example):**

  docker compose run --rm -e PYTHONPATH=/app api python scripts/purge_refresh_tokens.py

Requires ``DATABASE_URL`` in the environment (as with the API container).
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.auth_service import purge_stale_refresh_tokens


async def _tenant_ids(session: AsyncSession) -> list[UUID]:
    res = await session.execute(text("SELECT id FROM tenants ORDER BY id"))
    return [row[0] for row in res.fetchall()]


async def _run_for_tenant(session: AsyncSession, tenant_id: UUID) -> int:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )
    return await purge_stale_refresh_tokens(session)


async def main() -> int:
    try:
        database_url = os.environ["DATABASE_URL"]
    except KeyError as exc:
        print(f"Missing environment variable: {exc}", file=sys.stderr)
        return 1

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            async with session.begin():
                tid_env = os.environ.get("TENANT_ID")
                if tid_env:
                    tenant_id = UUID(tid_env.strip())
                    removed = await _run_for_tenant(session, tenant_id)
                else:
                    tenants = await _tenant_ids(session)
                    if not tenants:
                        print(
                            "No tenants found. For non-superuser roles set TENANT_ID "
                            "or use a connection that can read all tenants.",
                            file=sys.stderr,
                        )
                        return 2
                    removed = 0
                    for tid in tenants:
                        removed += await _run_for_tenant(session, tid)
        print(f"Deleted {removed} stale refresh token row(s).")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
