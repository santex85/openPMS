"""Webhook delivery retries and delivery log rows (mocked HTTP)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.webhook_secrets import encrypt_webhook_secret
from app.models.core.tenant import Tenant
from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.services.webhook_delivery_engine import deliver_to_subscription

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_deliver_to_subscription_logs_three_attempts_before_success() -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    sub_id = uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = get_settings()
    enc_secret = encrypt_webhook_secret(settings, "test-secret-key-for-hmac")

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
                    name="WHTest",
                    billing_email="wh@example.com",
                    status="active",
                ),
            )
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    events=["booking.created"],
                    secret=enc_secret,
                    is_active=True,
                ),
            )

    attempts = {"n": 0}

    async def fake_post(*_a, **_kw):
        attempts["n"] += 1
        r = MagicMock()
        r.status_code = 200 if attempts["n"] >= 3 else 500
        return r

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            sub = await session.get(WebhookSubscription, sub_id)

    with (
        patch(
            "app.services.webhook_delivery_engine.httpx.AsyncClient",
            return_value=mock_cm,
        ),
        patch(
            "app.services.webhook_delivery_engine.asyncio.sleep", new_callable=AsyncMock
        ),
    ):
        await deliver_to_subscription(
            factory,
            tenant_id,
            sub,
            "booking.created",
            {"x": 1},
        )

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        cnt = await session.scalar(
            select(func.count())
            .select_from(WebhookDeliveryLog)
            .where(
                WebhookDeliveryLog.webhook_subscription_id == sub_id,
            ),
        )
        assert cnt == 3
        rows = (
            (
                await session.execute(
                    select(WebhookDeliveryLog.attempt_number).where(
                        WebhookDeliveryLog.webhook_subscription_id == sub_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert sorted(rows) == [1, 2, 3]

    await engine.dispose()


@pytest.mark.asyncio
async def test_deliver_first_success_writes_single_log_with_200() -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    sub_id = uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = get_settings()
    enc_secret = encrypt_webhook_secret(settings, "whsec_plain_signing_secret_test")

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
                    name="WHTest2",
                    billing_email="wh2@example.com",
                    status="active",
                ),
            )
            session.add(
                WebhookSubscription(
                    id=sub_id,
                    tenant_id=tenant_id,
                    url="https://example.com/hook-ok",
                    events=["booking.created"],
                    secret=enc_secret,
                    is_active=True,
                ),
            )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        sub = await session.get(WebhookSubscription, sub_id)

    with patch(
        "app.services.webhook_delivery_engine.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        await deliver_to_subscription(
            factory,
            tenant_id,
            sub,
            "booking.created",
            {"id": "x"},
        )

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        cnt = await session.scalar(
            select(func.count())
            .select_from(WebhookDeliveryLog)
            .where(WebhookDeliveryLog.webhook_subscription_id == sub_id),
        )
        assert cnt == 1
        row = await session.scalar(
            select(WebhookDeliveryLog).where(
                WebhookDeliveryLog.webhook_subscription_id == sub_id,
            ),
        )
        assert row is not None
        assert row.http_status_code == 200
        assert row.error_message is None

    await engine.dispose()
