"""Webhook subscriptions (JWT only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import SessionDep, TenantIdDep, require_jwt_user, require_roles, require_scopes
from app.core.api_scopes import WEBHOOKS_READ, WEBHOOKS_WRITE
from app.schemas.webhooks import (
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreateResponse,
    WebhookSubscriptionPatch,
    WebhookSubscriptionRead,
)
from app.services.audit_service import record_audit
from app.services.webhook_subscription_service import WebhookSubscriptionError, create_subscription, list_subscriptions, patch_subscription

router = APIRouter()

WebhooksReadDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(WEBHOOKS_READ)),
]
WebhooksWriteDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(WEBHOOKS_WRITE)),
]


@router.get(
    "/subscriptions",
    response_model=list[WebhookSubscriptionRead],
    summary="List webhook subscriptions",
)
async def list_webhook_subscriptions(
    _: WebhooksReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[WebhookSubscriptionRead]:
    rows = await list_subscriptions(session, tenant_id)
    return [WebhookSubscriptionRead.model_validate(r) for r in rows]


@router.post(
    "/subscriptions",
    response_model=WebhookSubscriptionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create webhook subscription",
    description="Returns signing secret once. Subscriptions must use HTTPS.",
)
async def create_webhook_subscription(
    _: WebhooksWriteDep,
    body: WebhookSubscriptionCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> WebhookSubscriptionCreateResponse:
    try:
        row, secret = await create_subscription(
            session,
            tenant_id,
            url=body.url,
            events=body.events,
            is_active=body.is_active,
        )
    except WebhookSubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="webhook_subscription.create",
        entity_type="webhook_subscription",
        entity_id=row.id,
        new_values={
            "url": row.url,
            "events": list(row.events or []),
            "is_active": row.is_active,
        },
    )
    base = WebhookSubscriptionRead.model_validate(row)
    return WebhookSubscriptionCreateResponse(**base.model_dump(), secret=secret)


@router.patch(
    "/subscriptions/{subscription_id}",
    response_model=WebhookSubscriptionRead,
    summary="Update webhook subscription",
)
async def patch_webhook_subscription(
    _: WebhooksWriteDep,
    subscription_id: UUID,
    body: WebhookSubscriptionPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> WebhookSubscriptionRead:
    data = body.model_dump(exclude_unset=True)
    try:
        row = await patch_subscription(
            session,
            tenant_id,
            subscription_id,
            url=data.get("url"),
            events=data.get("events"),
            is_active=data.get("is_active"),
        )
    except WebhookSubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="webhook_subscription.patch",
        entity_type="webhook_subscription",
        entity_id=subscription_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return WebhookSubscriptionRead.model_validate(row)
