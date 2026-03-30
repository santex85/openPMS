"""Rate plans REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import RATE_PLANS_READ, RATE_PLANS_WRITE
from app.schemas.rate_plan import RatePlanCreate, RatePlanPatch, RatePlanRead
from app.services.audit_service import record_audit
from app.services.rate_plan_service import (
    RatePlanServiceError,
    create_rate_plan,
    delete_rate_plan,
    get_rate_plan,
    list_rate_plans,
    patch_rate_plan,
)

router = APIRouter()

RatePlanReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(RATE_PLANS_READ)),
]
RatePlanWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(RATE_PLANS_WRITE)),
]


@router.get("", response_model=list[RatePlanRead])
async def get_rate_plans(
    _: RatePlanReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID | None = Query(None, description="Filter by property"),
) -> list[RatePlanRead]:
    rows = await list_rate_plans(session, tenant_id, property_id=property_id)
    return [RatePlanRead.model_validate(r) for r in rows]


@router.get("/{rate_plan_id}", response_model=RatePlanRead)
async def get_rate_plan_by_id(
    rate_plan_id: UUID,
    _: RatePlanReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RatePlanRead:
    row = await get_rate_plan(session, tenant_id, rate_plan_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="rate plan not found"
        )
    return RatePlanRead.model_validate(row)


@router.post("", response_model=RatePlanRead, status_code=status.HTTP_201_CREATED)
async def post_rate_plan(
    _: RatePlanWriteRolesDep,
    body: RatePlanCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RatePlanRead:
    try:
        row = await create_rate_plan(session, tenant_id, body)
    except RatePlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="rate_plan.create",
        entity_type="rate_plan",
        entity_id=row.id,
        new_values=RatePlanRead.model_validate(row).model_dump(mode="json"),
    )
    return RatePlanRead.model_validate(row)


@router.patch("/{rate_plan_id}", response_model=RatePlanRead)
async def patch_rate_plan_by_id(
    rate_plan_id: UUID,
    _: RatePlanWriteRolesDep,
    body: RatePlanPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RatePlanRead:
    try:
        row = await patch_rate_plan(session, tenant_id, rate_plan_id, body)
    except RatePlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="rate_plan.patch",
        entity_type="rate_plan",
        entity_id=rate_plan_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return RatePlanRead.model_validate(row)


@router.delete("/{rate_plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_plan_by_id(
    rate_plan_id: UUID,
    _: RatePlanWriteRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    try:
        await delete_rate_plan(session, tenant_id, rate_plan_id)
    except RatePlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="rate_plan.delete",
        entity_type="rate_plan",
        entity_id=rate_plan_id,
        new_values={"deleted": True},
    )
