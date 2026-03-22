"""Properties REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantIdDep, get_db
from app.schemas.property import PropertyCreate, PropertyRead
from app.services import property_service

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "",
    response_model=PropertyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_property(
    body: PropertyCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PropertyRead:
    prop = await property_service.create_property(session, tenant_id, body)
    return PropertyRead.model_validate(prop)


@router.get("", response_model=list[PropertyRead])
async def list_properties(
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[PropertyRead]:
    rows = await property_service.list_properties(session, tenant_id)
    return [PropertyRead.model_validate(r) for r in rows]


@router.get("/{property_id}", response_model=PropertyRead)
async def get_property(
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
