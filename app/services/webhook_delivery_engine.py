"""HTTP webhook delivery with HMAC signing, retries, and delivery logs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.services.webhook_subscription_service import list_matching_subscriptions

# Sleeps after failed attempt 1 and 2 before retries (3 total attempts).
_RETRY_SLEEP_SEC = (10, 60)


def sign_webhook_body(secret: str, body_bytes: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    return digest


async def _set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )


async def _append_delivery_log(
    session: AsyncSession,
    tenant_id: UUID,
    subscription_id: UUID,
    event_type: str,
    attempt_number: int,
    *,
    http_status_code: int | None,
    error_message: str | None,
    payload: dict[str, Any],
) -> None:
    session.add(
        WebhookDeliveryLog(
            id=uuid4(),
            tenant_id=tenant_id,
            webhook_subscription_id=subscription_id,
            event_type=event_type,
            attempt_number=attempt_number,
            http_status_code=http_status_code,
            error_message=error_message,
            payload_json=payload,
        ),
    )
    await session.flush()


async def deliver_to_subscription(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    sub: WebhookSubscription,
    event_type: str,
    data: dict[str, Any],
) -> None:
    envelope: dict[str, Any] = {"event": event_type, "data": data}
    body_str = json.dumps(envelope, default=str, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")
    signature = sign_webhook_body(sub.secret, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt_idx in range(3):
            attempt_no = attempt_idx + 1
            http_status: int | None = None
            err_msg: str | None = None
            try:
                response = await client.post(
                    sub.url,
                    content=body_bytes,
                    headers=headers,
                )
                http_status = response.status_code
                if 200 <= response.status_code < 300:
                    async with factory() as session:
                        async with session.begin():
                            await _set_tenant(session, tenant_id)
                            await _append_delivery_log(
                                session,
                                tenant_id,
                                sub.id,
                                event_type,
                                attempt_no,
                                http_status_code=http_status,
                                error_message=None,
                                payload=envelope,
                            )
                    return
                err_msg = f"HTTP {response.status_code}"
            except Exception as exc:
                err_msg = str(exc)[:4000]

            async with factory() as session:
                async with session.begin():
                    await _set_tenant(session, tenant_id)
                    await _append_delivery_log(
                        session,
                        tenant_id,
                        sub.id,
                        event_type,
                        attempt_no,
                        http_status_code=http_status,
                        error_message=err_msg,
                        payload=envelope,
                    )

            if attempt_idx < 2:
                await asyncio.sleep(_RETRY_SLEEP_SEC[attempt_idx])


async def dispatch_webhook_event(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    event_type: str,
    data: dict[str, Any],
) -> None:
    async with factory() as session:
        async with session.begin():
            await _set_tenant(session, tenant_id)
            subs = await list_matching_subscriptions(session, tenant_id, event_type)

    for sub in subs:
        await deliver_to_subscription(factory, tenant_id, sub, event_type, data)
