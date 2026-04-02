"""Inventory and availability API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import ValidationError

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import INVENTORY_READ, INVENTORY_WRITE
from app.core.rate_limit import limiter
from app.schemas.availability_override import (
    AvailabilityOverridePutRequest,
    AvailabilityOverridePutResponse,
)
from app.schemas.inventory import AvailabilityGridResponse, AvailabilityQueryParams
from app.schemas.rooms import AssignableRoomsQueryParams, RoomRead
from app.services import availability_service
from app.services.room_assignable_service import list_assignable_rooms_for_stay
from app.services.availability_lock import LedgerNotSeededError
from app.services.availability_override_service import (
    AvailabilityOverrideError,
    apply_blocked_rooms_override,
)
from app.services.audit_service import record_audit
from app.services.webhook_runner import run_availability_after_override

router = APIRouter(prefix="/inventory", tags=["inventory"])

InventoryReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(INVENTORY_READ)),
]

InventoryWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(INVENTORY_WRITE)),
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


def _rooms_for_stay_query_params(
    property_id: Annotated[UUID, Query(description="Property scope")],
    room_type_id: Annotated[UUID, Query(description="Room category")],
    check_in: Annotated[date, Query(description="First night (inclusive)")],
    check_out: Annotated[
        date,
        Query(description="Exclusive checkout date (last night not included)"),
    ],
) -> AssignableRoomsQueryParams:
    try:
        return AssignableRoomsQueryParams.model_validate(
            {
                "property_id": property_id,
                "room_type_id": room_type_id,
                "check_in": check_in,
                "check_out": check_out,
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


@router.get("/rooms-for-stay", response_model=list[RoomRead])
async def get_inventory_rooms_for_stay(
    _: InventoryReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    params: Annotated[
        AssignableRoomsQueryParams,
        Depends(_rooms_for_stay_query_params),
    ],
) -> list[RoomRead]:
    """Physical rooms free on stay nights; lives under /inventory to avoid /rooms/{{id}} clash."""
    rows = await list_assignable_rooms_for_stay(session, tenant_id, params)
    if rows is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property or room type not found",
        )
    return [RoomRead.model_validate(r) for r in rows]


@router.put(
    "/availability/overrides",
    response_model=AvailabilityOverridePutResponse,
)
@limiter.limit("30/minute")
async def put_availability_overrides(
    request: Request,
    background_tasks: BackgroundTasks,
    _: InventoryWriteRolesDep,
    body: AvailabilityOverridePutRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> AvailabilityOverridePutResponse:
    try:
        n, room_type_id, dates = await apply_blocked_rooms_override(
            session,
            tenant_id,
            body,
        )
    except LedgerNotSeededError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AvailabilityOverrideError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="inventory.availability_override.put",
        entity_type="availability_ledger",
        entity_id=body.room_type_id,
        new_values=body.model_dump(mode="json") | {"dates_updated": n},
    )
    if dates:
        factory = request.app.state.async_session_factory
        background_tasks.add_task(
            run_availability_after_override,
            factory,
            tenant_id,
            room_type_id,
            dates,
        )
    return AvailabilityOverridePutResponse(dates_updated=n)
