"""Top-level assignable-room listing (avoids GET /rooms/{room_id} shadowing nested paths)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import SessionDep, TenantIdDep
from app.api.routes.inventory import InventoryReadRolesDep, rooms_for_stay_query_params
from app.core.rate_limit import limiter
from app.schemas.rooms import AssignableRoomsQueryParams, RoomRead
from app.services.room_assignable_service import list_assignable_rooms_for_stay

router = APIRouter(tags=["rooms"])


@router.get(
    "/assignable-rooms-for-stay",
    response_model=list[RoomRead],
    deprecated=True,
    summary="Assignable rooms for stay (deprecated)",
    description=(
        "Deprecated: use **GET /inventory/rooms-for-stay** instead. "
        "This route remains for backward compatibility."
    ),
)
@limiter.limit("60/minute")
async def get_assignable_rooms_for_stay_at_root(
    request: Request,
    response: Response,
    _: InventoryReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    params: Annotated[
        AssignableRoomsQueryParams,
        Depends(rooms_for_stay_query_params),
    ],
) -> list[RoomRead]:
    """Same as GET /inventory/rooms-for-stay; not under /rooms so {room_id} cannot swallow the path."""
    _ = request
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</inventory/rooms-for-stay>; rel="successor-version"'
    rows = await list_assignable_rooms_for_stay(session, tenant_id, params)
    if rows is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property or room type not found",
        )
    return [RoomRead.model_validate(r) for r in rows]
