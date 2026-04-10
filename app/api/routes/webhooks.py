"""Webhook subscriptions (JWT only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_jwt_user,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import WEBHOOKS_READ, WEBHOOKS_WRITE
from app.models.integrations.webhook_delivery_log import WebhookDeliveryLog
from app.schemas.webhooks import (
    WebhookDeliveryLogRead,
    WebhookSecretsReencryptRequest,
    WebhookSecretsReencryptResponse,
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreateResponse,
    WebhookSubscriptionPatch,
    WebhookSubscriptionRead,
)
from app.services.audit_service import record_audit
from app.services.webhook_subscription_service import (
    WebhookSubscriptionError,
    create_subscription,
    delete_subscription,
    list_subscriptions,
    patch_subscription,
    reencrypt_subscription_secrets_for_tenant,
)
from app.core.rate_limit import limiter

router = APIRouter()

WebhooksReadDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_jwt_user(),
            require_roles("owner", "manager"),
            require_scopes(WEBHOOKS_READ),
        ),
    ),
]
WebhooksWriteDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_jwt_user(),
            require_roles("owner", "manager"),
            require_scopes(WEBHOOKS_WRITE),
        ),
    ),
]

ReencryptWebhookSecretsDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_jwt_user(),
            require_roles("owner"),
            require_scopes(WEBHOOKS_WRITE),
        ),
    ),
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


@router.get(
    "/delivery-logs",
    response_model=list[WebhookDeliveryLogRead],
    summary="List webhook delivery attempts",
)
async def list_webhook_delivery_logs(
    _: WebhooksReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[WebhookDeliveryLogRead]:
    stmt = (
        select(WebhookDeliveryLog)
        .where(WebhookDeliveryLog.tenant_id == tenant_id)
        .order_by(WebhookDeliveryLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [WebhookDeliveryLogRead.model_validate(r) for r in result.scalars()]


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


@router.post(
    "/subscriptions/reencrypt-secrets",
    response_model=WebhookSecretsReencryptResponse,
    summary="Re-encrypt webhook signing secrets (Fernet rotation)",
    description=(
        "Owner-only. Decrypts each subscription secret using the key the API is "
        "currently running with, then encrypts again with ``new_fernet_key``. "
        "Deploy that same value as **WEBHOOK_SECRET_FERNET_KEY** and restart all "
        "instances before the next rotation so verification and further encrypts match."
    ),
)
@limiter.limit("30/minute")
async def reencrypt_webhook_subscription_secrets(
    request: Request,
    _: ReencryptWebhookSecretsDep,
    body: WebhookSecretsReencryptRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> WebhookSecretsReencryptResponse:
    _ = request
    try:
        updated = await reencrypt_subscription_secrets_for_tenant(
            session,
            tenant_id,
            body.new_fernet_key,
        )
    except WebhookSubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="webhook_subscription.secrets_reencrypt",
        entity_type="webhook_subscription",
        entity_id=None,
        new_values={"updated_count": updated},
    )
    return WebhookSecretsReencryptResponse(updated_count=updated)


@router.patch(
    "/subscriptions/{subscription_id}",
    response_model=WebhookSubscriptionRead,
    summary="Update webhook subscription",
)
@limiter.limit("120/minute")
async def patch_webhook_subscription(
    request: Request,
    _: WebhooksWriteDep,
    subscription_id: UUID,
    body: WebhookSubscriptionPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> WebhookSubscriptionRead:
    _ = request
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


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete webhook subscription",
    description="Removes the subscription. Delivery logs are removed via ON DELETE CASCADE.",
)
@limiter.limit("120/minute")
async def delete_webhook_subscription(
    request: Request,
    _: WebhooksWriteDep,
    subscription_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    _ = request
    try:
        await delete_subscription(session, tenant_id, subscription_id)
    except WebhookSubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="webhook_subscription.delete",
        entity_type="webhook_subscription",
        entity_id=subscription_id,
        new_values={},
    )
