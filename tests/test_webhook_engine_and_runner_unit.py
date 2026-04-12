"""Unit tests for webhook queue processing and booking patch webhooks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core.tenant import Tenant
from app.models.integrations.webhook_pending_delivery import WebhookPendingDelivery
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.services.webhook_delivery_engine import (
    _process_one_pending_row,
    process_webhook_delivery_queue_batch,
    webhook_delivery_worker_loop,
)
from app.services.webhook_runner import run_booking_patch_webhooks

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url


@pytest.mark.asyncio
async def test_process_one_pending_row_removes_when_event_not_subscribed(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid = uuid4()
    sub_id = uuid4()
    pending_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                Tenant(
                    id=tid,
                    name="WHQueueTenant",
                    billing_email="whq@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tid,
                    url="https://example.com/hook",
                    events=["other.only"],
                    secret="s" * 32,
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookPendingDelivery(
                    id=pending_id,
                    tenant_id=tid,
                    webhook_subscription_id=sub_id,
                    event_type="booking.cancelled",
                    payload_json={
                        "event": "booking.cancelled",
                        "data": {"booking_id": str(uuid4())},
                    },
                    attempt_count=0,
                    next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=5),
                    status="pending",
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.internal_webhook_worker', 'true', true)"),
            )
            row = await session.get(WebhookPendingDelivery, pending_id)
            assert row is not None
            await _process_one_pending_row(session, row)

    async with factory() as session:
        gone = await session.get(WebhookPendingDelivery, pending_id)
    assert gone is None


@pytest.mark.asyncio
async def test_webhook_delivery_worker_loop_respects_stop_event(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    stop = asyncio.Event()
    stop.set()
    await webhook_delivery_worker_loop(factory, stop)


@pytest.mark.asyncio
async def test_run_booking_patch_webhooks_cancelled_dispatches(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = uuid4()
    bid = uuid4()

    with patch(
        "app.services.webhook_runner.dispatch_webhook_event",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        await run_booking_patch_webhooks(
            factory,
            tid,
            bid,
            before={"status": "confirmed"},
            after={"status": "cancelled"},
            cancellation_reason="guest",
            folio_balance_on_checkout=None,
        )
        mock_dispatch.assert_awaited()


@pytest.mark.asyncio
async def test_process_webhook_queue_batch_returns_zero_when_empty(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    n = await process_webhook_delivery_queue_batch(factory, max_rows=2)
    assert n == 0
