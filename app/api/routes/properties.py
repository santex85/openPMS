"""Properties REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import PROPERTIES_READ, PROPERTIES_WRITE
from app.schemas.country_pack import PropertyLockStatusRead
from app.schemas.property import PropertyCreate, PropertyPatch, PropertyRead
from app.schemas.tax_config import TaxConfigCreate, TaxConfigRead
from app.services import property_service, tax_service
from app.services.audit_service import record_audit
from app.core.rate_limit import limiter

router = APIRouter()

PropertyReadRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager", "viewer", "receptionist"),
            require_scopes(PROPERTIES_READ),
        ),
    ),
]
PropertyWriteRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(PROPERTIES_WRITE),
        ),
    ),
]
PropertyTaxOwnerWriteDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner"),
            require_scopes(PROPERTIES_WRITE),
        ),
    ),
]


@router.post(
    "",
    response_model=PropertyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_property(
    _: PropertyWriteRolesDep,
    body: PropertyCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyRead:
    prop = await property_service.create_property(session, tenant_id, body)
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="property.create",
        entity_type="property",
        entity_id=prop.id,
        new_values=PropertyRead.model_validate(prop).model_dump(mode="json"),
    )
    return PropertyRead.model_validate(prop)


@router.get("", response_model=list[PropertyRead])
async def list_properties(
    _: PropertyReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[PropertyRead]:
    rows = await property_service.list_properties(session, tenant_id)
    return [PropertyRead.model_validate(r) for r in rows]


@router.get("/{property_id}/lock-status", response_model=PropertyLockStatusRead)
async def get_property_lock_status(
    _: PropertyReadRolesDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyLockStatusRead:
    prop = await property_service.get_property(session, tenant_id, property_id)
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    booking_count = await property_service.count_bookings_for_property(
        session,
        tenant_id,
        property_id,
    )
    return PropertyLockStatusRead(
        property_id=property_id,
        country_pack_locked=booking_count > 0,
        booking_count=booking_count,
    )


@router.get(
    "/{property_id}/tax-config",
    response_model=TaxConfigRead,
)
async def get_property_tax_config(
    _: PropertyReadRolesDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> TaxConfigRead:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    row = await tax_service.get_tax_config(session, tenant_id, property_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tax config not found",
        )
    return TaxConfigRead.model_validate(row)


@router.put(
    "/{property_id}/tax-config",
    response_model=TaxConfigRead,
)
async def put_property_tax_config(
    _: PropertyTaxOwnerWriteDep,
    property_id: UUID,
    body: TaxConfigCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> TaxConfigRead:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    row = await tax_service.upsert_tax_config(session, tenant_id, property_id, body)
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="property.tax_config.upsert",
        entity_type="property",
        entity_id=property_id,
        new_values=body.model_dump(mode="json"),
    )
    return TaxConfigRead.model_validate(row)


@router.delete(
    "/{property_id}/tax-config",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_property_tax_config(
    _: PropertyTaxOwnerWriteDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    deleted = await tax_service.delete_tax_config(session, tenant_id, property_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tax config not found",
        )
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="property.tax_config.delete",
        entity_type="property",
        entity_id=property_id,
        new_values={"deleted": True},
    )


@router.get("/{property_id}", response_model=PropertyRead)
async def get_property(
    _: PropertyReadRolesDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyRead:
    prop = await property_service.get_property(session, tenant_id, property_id)
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    return PropertyRead.model_validate(prop)


@router.patch("/{property_id}", response_model=PropertyRead)
@limiter.limit("120/minute")
async def patch_property(
    request: Request,
    _: PropertyWriteRolesDep,
    property_id: UUID,
    body: PropertyPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyRead:
    _ = request
    prop = await property_service.update_property(session, tenant_id, property_id, body)
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="property.patch",
        entity_type="property",
        entity_id=property_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return PropertyRead.model_validate(prop)
