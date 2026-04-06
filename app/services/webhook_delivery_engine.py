"""HTTP webhook delivery with HMAC signing, retries, and delivery logs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.webhook_url_validation import (
    WebhookUrlUnsafeError,
    assert_webhook_target_ips_safe_for_url,
)
from app.core.webhook_secrets import decrypt_webhook_secret
from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.models.integrations.webhook_pending_delivery import WebhookPendingDelivery
from app.models.integrations.webhook_subscription import WebhookSubscription
from app.services.webhook_subscription_service import list_matching_subscriptions

log = structlog.get_logger()

# After failed attempts 1 and 2, wait before next worker pick (calendar backoff).
_QUEUE_RETRY_DELAYS_SEC = (10, 60)

# Sleeps after failed attempt 1 and 2 for direct deliver_to_subscription (tests).
_RETRY_SLEEP_SEC = (10, 60)


def sign_webhook_body(secret: str, body_bytes: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    return digest


async def _assert_webhook_url_safe_async(url: str) -> None:
    await asyncio.to_thread(assert_webhook_target_ips_safe_for_url, url)


async def _set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
        {"tid": str(tenant_id)},
    )


async def _set_internal_webhook_worker(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT set_config('app.internal_webhook_worker', 'true', true)"),
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


async def _single_http_delivery_attempt(
    *,
    url: str,
    body_bytes: bytes,
    headers: dict[str, str],
    client: httpx.AsyncClient,
) -> tuple[bool, int | None, str | None]:
    try:
        await _assert_webhook_url_safe_async(url)
    except WebhookUrlUnsafeError as exc:
        return False, None, str(exc)[:4000]
    try:
        response = await client.post(url, content=body_bytes, headers=headers)
    except Exception as exc:
        return False, None, str(exc)[:4000]
    if 200 <= response.status_code < 300:
        return True, response.status_code, None
    return False, response.status_code, f"HTTP {response.status_code}"


async def deliver_to_subscription(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    sub: WebhookSubscription,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Deliver with inline retries (used by tests; production uses the DB-backed queue)."""
    envelope: dict[str, Any] = {"event": event_type, "data": data}
    body_str = json.dumps(envelope, default=str, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")
    settings = get_settings()
    signing_secret = decrypt_webhook_secret(settings, sub.secret)
    signature = sign_webhook_body(signing_secret, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt_idx in range(3):
            attempt_no = attempt_idx + 1
            http_status: int | None = None
            err_msg: str | None = None
            ok, http_status, err_msg = await _single_http_delivery_attempt(
                url=sub.url,
                body_bytes=body_bytes,
                headers=headers,
                client=client,
            )
            if ok:
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
    envelope: dict[str, Any] = {"event": event_type, "data": data}
    now = datetime.now(tz=UTC)
    async with factory() as session:
        async with session.begin():
            await _set_tenant(session, tenant_id)
            subs = await list_matching_subscriptions(session, tenant_id, event_type)
            for sub in subs:
                session.add(
                    WebhookPendingDelivery(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        webhook_subscription_id=sub.id,
                        event_type=event_type,
                        payload_json=envelope,
                        attempt_count=0,
                        next_retry_at=now,
                        status="pending",
                    ),
                )


async def _process_one_pending_row(
    session: AsyncSession,
    row: WebhookPendingDelivery,
) -> None:
    tenant_id = row.tenant_id
    await _set_tenant(session, tenant_id)
    sub = await session.get(WebhookSubscription, row.webhook_subscription_id)
    envelope = dict(row.payload_json)
    ev = str(envelope.get("event") or row.event_type)
    attempt_no = row.attempt_count + 1

    if sub is None or not sub.is_active or ev not in sub.events:
        await _append_delivery_log(
            session,
            tenant_id,
            row.webhook_subscription_id,
            ev,
            attempt_no,
            http_status_code=None,
            error_message="subscription inactive, missing, or event no longer subscribed",
            payload=envelope,
        )
        await session.delete(row)
        return

    body_str = json.dumps(envelope, default=str, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")
    settings = get_settings()
    signing_secret = decrypt_webhook_secret(settings, sub.secret)
    signature = sign_webhook_body(signing_secret, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        ok, http_status, err_msg = await _single_http_delivery_attempt(
            url=sub.url,
            body_bytes=body_bytes,
            headers=headers,
            client=client,
        )

    if ok:
        await _append_delivery_log(
            session,
            tenant_id,
            sub.id,
            ev,
            attempt_no,
            http_status_code=http_status,
            error_message=None,
            payload=envelope,
        )
        await session.delete(row)
        return

    await _append_delivery_log(
        session,
        tenant_id,
        sub.id,
        ev,
        attempt_no,
        http_status_code=http_status,
        error_message=err_msg,
        payload=envelope,
    )

    if attempt_no >= 3:
        row.status = "dead_letter"
        row.attempt_count = attempt_no
        log.warning(
            "webhook_delivery_dead_letter",
            tenant_id=str(tenant_id),
            subscription_id=str(sub.id),
            event_type=ev,
            attempts=attempt_no,
        )
        return

    delay_idx = min(attempt_no - 1, len(_QUEUE_RETRY_DELAYS_SEC) - 1)
    delay = _QUEUE_RETRY_DELAYS_SEC[delay_idx]
    row.attempt_count = attempt_no
    row.next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)


async def process_webhook_delivery_queue_batch(
    factory: async_sessionmaker[AsyncSession],
    *,
    max_rows: int = 50,
) -> int:
    """Claim and process up to max_rows due pending deliveries (internal worker)."""
    processed = 0
    for _ in range(max_rows):
        now = datetime.now(tz=UTC)
        async with factory() as session:
            async with session.begin():
                await _set_internal_webhook_worker(session)
                row = await session.scalar(
                    select(WebhookPendingDelivery)
                    .where(
                        WebhookPendingDelivery.status == "pending",
                        WebhookPendingDelivery.next_retry_at <= now,
                    )
                    .order_by(WebhookPendingDelivery.next_retry_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True),
                )
                if row is None:
                    break
                await _process_one_pending_row(session, row)
        processed += 1

    return processed


async def webhook_delivery_worker_loop(
    factory: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
) -> None:
    while True:
        n = 0
        try:
            if stop.is_set():
                break
            n = await process_webhook_delivery_queue_batch(factory, max_rows=50)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("webhook_queue_batch_failed")
        delay = 1.0 if n else 5.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            break
        except TimeoutError:
            continue
