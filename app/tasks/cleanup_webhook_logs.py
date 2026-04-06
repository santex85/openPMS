"""Purge old webhook_delivery_logs rows (tenant-scoped RLS)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import create_async_engine_and_sessionmaker
from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog


async def _tenant_ids(session: AsyncSession) -> list[UUID]:
    res = await session.execute(text("SELECT id FROM tenants ORDER BY id"))
    return [row[0] for row in res.fetchall()]


async def cleanup_old_delivery_logs(session: AsyncSession, retention_days: int) -> int:
    """
    Delete delivery logs with created_at older than retention window.
    Iterates tenants and sets app.tenant_id so RLS allows deletes.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    tenants = await _tenant_ids(session)
    if not tenants:
        return 0
    deleted_total = 0
    for tid in tenants:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tid)},
        )
        stmt = delete(WebhookDeliveryLog).where(WebhookDeliveryLog.created_at < cutoff)
        result = await session.execute(stmt)
        deleted_total += int(result.rowcount or 0)
    return deleted_total


async def main() -> int:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with factory() as session:
            async with session.begin():
                n = await cleanup_old_delivery_logs(
                    session,
                    settings.webhook_log_retention_days,
                )
        print(
            f"Deleted {n} webhook delivery log row(s) older than "
            f"{settings.webhook_log_retention_days} day(s).",
        )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
