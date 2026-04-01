"""Properties REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import PROPERTIES_READ, PROPERTIES_WRITE
from app.schemas.property import PropertyCreate, PropertyPatch, PropertyRead
from app.services import property_service
from app.services.audit_service import record_audit

router = APIRouter()

PropertyReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(PROPERTIES_READ)),
]
PropertyWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(PROPERTIES_WRITE)),
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
async def patch_property(
    _: PropertyWriteRolesDep,
    property_id: UUID,
    body: PropertyPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyRead:
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
