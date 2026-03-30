"""Manage tenant API keys (JWT only; plaintext returned once on create)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    require_jwt_user,
    require_roles,
)
from app.schemas.api_keys import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyPatchRequest,
    ApiKeyRead,
)
from app.services.api_key_service import ApiKeyServiceError, create_api_key, list_api_keys, patch_api_key
from app.services.audit_service import record_audit

router = APIRouter()

ApiKeysManageDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
]


@router.get("", response_model=list[ApiKeyRead])
async def get_api_keys(
    _: ApiKeysManageDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[ApiKeyRead]:
    rows = await list_api_keys(session, tenant_id)
    return [ApiKeyRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_api_key(
    _: ApiKeysManageDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: ApiKeyCreateRequest,
) -> ApiKeyCreateResponse:
    try:
        row, plain = await create_api_key(
            session,
            tenant_id,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    except ApiKeyServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="api_key.create",
        entity_type="api_key",
        entity_id=row.id,
        new_values={
            "name": row.name,
            "scopes": list(body.scopes),
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        },
    )
    base = ApiKeyRead.model_validate(row)
    return ApiKeyCreateResponse(
        **base.model_dump(),
        key=plain,
    )


@router.patch("/{key_id}", response_model=ApiKeyRead)
async def patch_api_key_route(
    _: ApiKeysManageDep,
    key_id: UUID,
    body: ApiKeyPatchRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> ApiKeyRead:
    data = body.model_dump(exclude_unset=True)
    try:
        row = await patch_api_key(
            session,
            tenant_id,
            key_id,
            is_active=data.get("is_active"),
            name=data.get("name"),
        )
    except ApiKeyServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="api_key.patch",
        entity_type="api_key",
        entity_id=key_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return ApiKeyRead.model_validate(row)
