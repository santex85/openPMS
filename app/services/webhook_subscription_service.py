"""CRUD for webhook subscriptions."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.webhook_events import VALID_WEBHOOK_EVENTS
from app.core.webhook_secrets import encrypt_webhook_secret
from app.models.integrations.webhook_subscription import WebhookSubscription


class WebhookSubscriptionError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def _validate_https_url(url: str) -> str:
    trimmed = url.strip()
    if not trimmed:
        raise WebhookSubscriptionError("url is required", status_code=422)
    parsed = urlparse(trimmed)
    if parsed.scheme != "https":
        raise WebhookSubscriptionError("url must use HTTPS", status_code=422)
    if not parsed.netloc:
        raise WebhookSubscriptionError("url must include a host", status_code=422)
    return trimmed


def _normalize_events(events: list[str]) -> list[str]:
    out: list[str] = []
    for e in events:
        t = e.strip().lower()
        if not t:
            continue
        if t not in VALID_WEBHOOK_EVENTS:
            raise WebhookSubscriptionError(
                f"unknown event: {e!r}; valid: {sorted(VALID_WEBHOOK_EVENTS)}",
                status_code=422,
            )
        out.append(t)
    if not out:
        raise WebhookSubscriptionError(
            "at least one event is required", status_code=422
        )
    dedup: list[str] = []
    seen: set[str] = set()
    for e in out:
        if e not in seen:
            seen.add(e)
            dedup.append(e)
    return dedup


async def create_subscription(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    url: str,
    events: list[str],
    is_active: bool = True,
) -> tuple[WebhookSubscription, str]:
    u = _validate_https_url(url)
    ev = _normalize_events(events)
    plain_secret = generate_webhook_secret()
    settings = get_settings()
    row = WebhookSubscription(
        id=uuid4(),
        tenant_id=tenant_id,
        url=u,
        events=ev,
        secret=encrypt_webhook_secret(settings, plain_secret),
        is_active=is_active,
    )
    session.add(row)
    await session.flush()
    return row, plain_secret


async def list_subscriptions(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[WebhookSubscription]:
    stmt = (
        select(WebhookSubscription)
        .where(WebhookSubscription.tenant_id == tenant_id)
        .order_by(WebhookSubscription.url.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def patch_subscription(
    session: AsyncSession,
    tenant_id: UUID,
    subscription_id: UUID,
    *,
    url: str | None,
    events: list[str] | None,
    is_active: bool | None,
) -> WebhookSubscription:
    row = await session.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.tenant_id == tenant_id,
            WebhookSubscription.id == subscription_id,
        ),
    )
    if row is None:
        raise WebhookSubscriptionError("subscription not found", status_code=404)
    if url is not None:
        row.url = _validate_https_url(url)
    if events is not None:
        row.events = _normalize_events(events)
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return row


async def delete_subscription(
    session: AsyncSession,
    tenant_id: UUID,
    subscription_id: UUID,
) -> None:
    row = await session.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.tenant_id == tenant_id,
            WebhookSubscription.id == subscription_id,
        ),
    )
    if row is None:
        raise WebhookSubscriptionError("subscription not found", status_code=404)
    session.delete(row)
    await session.flush()


async def list_matching_subscriptions(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
) -> list[WebhookSubscription]:
    ev = event_type.strip().lower()
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == tenant_id,
        WebhookSubscription.is_active.is_(True),
        WebhookSubscription.events.contains([ev]),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
