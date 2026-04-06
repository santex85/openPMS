"""Housekeeping board and room status updates."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import (
    OptionalUserIdWriteDep,
    SessionDep,
    TenantIdDep,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import HOUSEKEEPING_READ, HOUSEKEEPING_WRITE
from app.schemas.housekeeping import (
    HousekeepingPatchRequest,
    HousekeepingPatchResponse,
    HousekeepingRoomRead,
)
from app.services.audit_service import record_audit
from app.services.housekeeping_service import (
    HousekeepingServiceError,
    list_rooms_for_housekeeping,
    patch_room_housekeeping,
)
from app.core.rate_limit import limiter

router = APIRouter(prefix="/housekeeping", tags=["housekeeping"])

HousekeepingReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "housekeeper", "receptionist")),
    Depends(require_scopes(HOUSEKEEPING_READ)),
]
HousekeepingWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "housekeeper")),
    Depends(require_scopes(HOUSEKEEPING_WRITE)),
]


@router.get("", response_model=list[HousekeepingRoomRead])
async def get_housekeeping_board(
    _: HousekeepingReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property to list rooms for"),
    hk_status: str | None = Query(
        None,
        alias="status",
        description="Filter by housekeeping_status",
    ),
    priority: str | None = Query(None, description="Filter by housekeeping_priority"),
    filter_date: date | None = Query(
        None,
        alias="date",
        description="Only rooms with a booking line on this night date",
    ),
) -> list[HousekeepingRoomRead]:
    try:
        return await list_rooms_for_housekeeping(
            session,
            tenant_id,
            property_id=property_id,
            housekeeping_status=hk_status,
            housekeeping_priority=priority,
            filter_date=filter_date,
        )
    except HousekeepingServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/{room_id}", response_model=HousekeepingPatchResponse)
@limiter.limit("120/minute")
async def patch_housekeeping_room(
    request: Request,
    room_id: UUID,
    _: HousekeepingWriteRolesDep,
    body: HousekeepingPatchRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    user_id: OptionalUserIdWriteDep,
) -> HousekeepingPatchResponse:
    _ = request
    try:
        room = await patch_room_housekeeping(
            session,
            tenant_id,
            room_id,
            body,
            actor_user_id=user_id,
        )
    except HousekeepingServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="housekeeping.room.patch",
        entity_type="room",
        entity_id=room_id,
        new_values={
            "housekeeping_status": body.housekeeping_status,
            **(
                {"housekeeping_priority": body.housekeeping_priority}
                if body.housekeeping_priority is not None
                else {}
            ),
        },
    )
    return HousekeepingPatchResponse(
        id=room.id,
        housekeeping_status=room.housekeeping_status,
        housekeeping_priority=room.housekeeping_priority,
    )
