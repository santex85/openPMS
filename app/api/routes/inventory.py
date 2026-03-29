"""Inventory and availability API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.api.deps import SessionDep, TenantIdDep, require_roles
from app.schemas.availability_override import AvailabilityOverridePutRequest, AvailabilityOverridePutResponse
from app.schemas.inventory import AvailabilityGridResponse, AvailabilityQueryParams
from app.services import availability_service
from app.services.availability_lock import LedgerNotSeededError
from app.services.availability_override_service import AvailabilityOverrideError, apply_blocked_rooms_override

router = APIRouter(prefix="/inventory", tags=["inventory"])

InventoryReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
]

InventoryWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
]


def _availability_query_params(
    property_id: Annotated[UUID, Query(description="Property to build the grid for")],
    start_date: Annotated[date, Query(description="Inclusive start (night)")],
    end_date: Annotated[date, Query(description="Inclusive end (night)")],
    room_type_id: Annotated[
        UUID | None,
        Query(description="Optional: single room type"),
    ] = None,
) -> AvailabilityQueryParams:
    try:
        return AvailabilityQueryParams.model_validate(
            {
                "property_id": property_id,
                "start_date": start_date,
                "end_date": end_date,
                "room_type_id": room_type_id,
            },
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


@router.get(
    "/availability",
    response_model=AvailabilityGridResponse,
)
async def get_availability_grid(
    _: InventoryReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    params: Annotated[AvailabilityQueryParams, Depends(_availability_query_params)],
) -> AvailabilityGridResponse:
    grid = await availability_service.get_availability_grid(
        session,
        tenant_id,
        property_id=params.property_id,
        start_date=params.start_date,
        end_date=params.end_date,
        room_type_id=params.room_type_id,
    )
    if grid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    return grid


@router.put(
    "/availability/overrides",
    response_model=AvailabilityOverridePutResponse,
)
async def put_availability_overrides(
    _: InventoryWriteRolesDep,
    body: AvailabilityOverridePutRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> AvailabilityOverridePutResponse:
    try:
        n = await apply_blocked_rooms_override(session, tenant_id, body)
    except LedgerNotSeededError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AvailabilityOverrideError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return AvailabilityOverridePutResponse(dates_updated=n)
