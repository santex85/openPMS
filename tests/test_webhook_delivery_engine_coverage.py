"""Coverage for app.services.webhook_delivery_engine (queue + worker loop)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.webhook_secrets import encrypt_webhook_secret
from app.models.core.tenant import Tenant
from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.models.integrations.webhook_pending_delivery import WebhookPendingDelivery
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.services.webhook_delivery_engine import (
    _process_one_pending_row,
    dispatch_webhook_event,
    webhook_delivery_worker_loop,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def _encrypted_secret() -> str:
    settings = get_settings()
    return encrypt_webhook_secret(settings, "cov-webhook-secret-key-32bytes!")


@pytest.mark.asyncio
async def test_dispatch_webhook_event_enqueues_pending_rows(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    sub_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="EngCov",
                    billing_email="ec@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created", "availability.changed"],
                    secret=_encrypted_secret(),
                    is_active=True,
                ),
            )
            await session.flush()

    await dispatch_webhook_event(
        factory,
        tenant_id,
        "booking.created",
        {"booking_id": str(uuid4())},
    )

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        n = await session.scalar(
            select(func.count()).select_from(WebhookPendingDelivery).where(
                WebhookPendingDelivery.tenant_id == tenant_id,
            ),
        )
        assert int(n or 0) == 1


@pytest.mark.asyncio
async def test_process_one_pending_row_success_deletes_row(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    sub_id = uuid4()
    pending_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    payload = {"event": "booking.created", "data": {"x": 1}}

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="EngCov2",
                    billing_email="ec2@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created"],
                    secret=_encrypted_secret(),
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookPendingDelivery(
                    id=pending_id,
                    tenant_id=tenant_id,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    payload_json=payload,
                    attempt_count=0,
                    next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
                    status="pending",
                ),
            )

    mock_client = MagicMock()
    mock_client.post = AsyncMock(
        return_value=MagicMock(status_code=200),
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.webhook_delivery_engine.httpx.AsyncClient",
        return_value=mock_cm,
    ):
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

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        log_row = await session.scalar(
            select(WebhookDeliveryLog).where(
                WebhookDeliveryLog.webhook_subscription_id == sub_id,
            ),
        )
        assert log_row is not None
        assert log_row.http_status_code == 200


@pytest.mark.asyncio
async def test_process_one_pending_row_failure_retries(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    sub_id = uuid4()
    pending_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    payload = {"event": "booking.created", "data": {}}

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="EngCov3",
                    billing_email="ec3@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created"],
                    secret=_encrypted_secret(),
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookPendingDelivery(
                    id=pending_id,
                    tenant_id=tenant_id,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    payload_json=payload,
                    attempt_count=0,
                    next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
                    status="pending",
                ),
            )

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.webhook_delivery_engine.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.internal_webhook_worker', 'true', true)"),
                )
                row = await session.get(WebhookPendingDelivery, pending_id)
                assert row is not None
                await _process_one_pending_row(session, row)

    async with factory() as session:
        row2 = await session.get(WebhookPendingDelivery, pending_id)
        assert row2 is not None
    assert row2.attempt_count == 1
    assert row2.status == "pending"
    assert row2.next_retry_at > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_process_one_pending_row_dead_letter(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    sub_id = uuid4()
    pending_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    payload = {"event": "booking.created", "data": {}}

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="EngCov4",
                    billing_email="ec4@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created"],
                    secret=_encrypted_secret(),
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookPendingDelivery(
                    id=pending_id,
                    tenant_id=tenant_id,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    payload_json=payload,
                    attempt_count=2,
                    next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
                    status="pending",
                ),
            )

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.webhook_delivery_engine.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.internal_webhook_worker', 'true', true)"),
                )
                row = await session.get(WebhookPendingDelivery, pending_id)
                assert row is not None
                await _process_one_pending_row(session, row)

    async with factory() as session:
        row2 = await session.get(WebhookPendingDelivery, pending_id)
    assert row2 is not None
    assert row2.status == "dead_letter"
    assert row2.attempt_count == 3


@pytest.mark.asyncio
async def test_webhook_delivery_worker_loop_processes_then_stops(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    sub_id = uuid4()
    pending_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    payload = {"event": "booking.created", "data": {"id": "1"}}

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="EngCov5",
                    billing_email="ec5@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created"],
                    secret=_encrypted_secret(),
                    is_active=True,
                ),
            )
            await session.flush()
            session.add(
                WebhookPendingDelivery(
                    id=pending_id,
                    tenant_id=tenant_id,
                    webhook_subscription_id=sub_id,
                    event_type="booking.created",
                    payload_json=payload,
                    attempt_count=0,
                    next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
                    status="pending",
                ),
            )

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    stop = asyncio.Event()

    async def _arm_stop() -> None:
        await asyncio.sleep(0.15)
        stop.set()

    with patch(
        "app.services.webhook_delivery_engine.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        await asyncio.wait_for(
            asyncio.gather(
                webhook_delivery_worker_loop(factory, stop),
                _arm_stop(),
            ),
            timeout=5.0,
        )

    async with factory() as session:
        gone = await session.get(WebhookPendingDelivery, pending_id)
    assert gone is None
