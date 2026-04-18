"""Tests for cleanup_old_delivery_logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.models.core.tenant import Tenant
from app.tasks.cleanup_webhook_logs import cleanup_old_delivery_logs

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url


@pytest.mark.asyncio
async def test_cleanup_old_delivery_logs_deletes_only_stale_rows(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    tid = uuid4()
    sub_id = uuid4()
    old_id = uuid4()
    new_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            session.add(
                Tenant(
                    id=tid,
                    name="LogCleanTenant",
                    billing_email="lc@example.com",
                    status="active",
                ),
            )
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tid,
                    url="https://example.com/out",
                    events=["booking.created"],
                    secret="test-secret-token-not-used-in-delete",
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookDeliveryLog(
                    id=old_id,
                    tenant_id=tid,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    attempt_number=1,
                    http_status_code=200,
                    error_message=None,
                    payload_json={"event": "booking.created"},
                    created_at=datetime.now(UTC) - timedelta(days=400),
                ),
            )
            session.add(
                WebhookDeliveryLog(
                    id=new_id,
                    tenant_id=tid,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    attempt_number=1,
                    http_status_code=200,
                    error_message=None,
                    payload_json={"event": "booking.created"},
                    created_at=datetime.now(UTC) - timedelta(days=1),
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            deleted = await cleanup_old_delivery_logs(session, retention_days=365)

    assert deleted >= 1

    async with factory() as session:
        old_row = await session.get(WebhookDeliveryLog, old_id)
        new_row = await session.get(WebhookDeliveryLog, new_id)
    assert old_row is None
    assert new_row is not None


@pytest.mark.asyncio
async def test_cleanup_returns_zero_when_no_tenants(
    db_engine: object, monkeypatch
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    from app.tasks import cleanup_webhook_logs as mod

    async def _empty(_session: AsyncSession) -> list:
        return []

    monkeypatch.setattr(mod, "_tenant_ids", _empty)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            n = await cleanup_old_delivery_logs(session, retention_days=30)
    assert n == 0
