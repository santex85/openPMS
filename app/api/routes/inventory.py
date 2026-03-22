"""Inventory and availability API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantIdDep, get_db
from app.schemas.inventory import AvailabilityGridResponse, AvailabilityQueryParams
from app.services import availability_service

router = APIRouter(prefix="/inventory", tags=["inventory"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]


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
