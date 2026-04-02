"""Rooms REST API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import ROOMS_READ, ROOMS_WRITE
from app.schemas.rooms import (
    AssignableRoomsQueryParams,
    RoomCreate,
    RoomPatch,
    RoomRead,
)
from app.services.room_assignable_service import list_assignable_rooms_for_stay
from app.services.audit_service import record_audit
from app.services.room_list_service import property_belongs_to_tenant
from app.services.room_service import (
    RoomServiceError,
    create_room,
    get_room,
    list_rooms,
    patch_room,
    soft_delete_room,
)

router = APIRouter()

RoomReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist", "housekeeper")),
    Depends(require_scopes(ROOMS_READ)),
]
RoomWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(ROOMS_WRITE)),
]


async def _ensure_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> None:
    ok = await property_belongs_to_tenant(session, tenant_id, property_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property not found",
        )


def _assignable_rooms_query_params(
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


@router.get("", response_model=list[RoomRead])
async def get_rooms(
    _: RoomReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID | None = Query(
        None,
        description="Filter by property; omit to list all rooms for the tenant",
    ),
) -> list[RoomRead]:
    if property_id is not None:
        await _ensure_property(session, tenant_id, property_id)
    rows = await list_rooms(session, tenant_id, property_id=property_id)
    return [RoomRead.model_validate(r) for r in rows]


@router.get("/for-stay", response_model=list[RoomRead])
async def get_rooms_assignable_for_stay(
    _: RoomReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    params: Annotated[
        AssignableRoomsQueryParams,
        Depends(_assignable_rooms_query_params),
    ],
) -> list[RoomRead]:
    """Rooms free on [check_in, check_out); static path avoids /rooms/{room_id} shadowing."""
    rows = await list_assignable_rooms_for_stay(session, tenant_id, params)
    if rows is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property or room type not found",
        )
    return [RoomRead.model_validate(r) for r in rows]


@router.get("/{room_id}", response_model=RoomRead)
async def get_room_by_id(
    room_id: UUID,
    _: RoomReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomRead:
    row = await get_room(session, tenant_id, room_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="room not found",
        )
    return RoomRead.model_validate(row)


@router.post("", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
async def post_room(
    _: RoomWriteRolesDep,
    body: RoomCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomRead:
    try:
        row = await create_room(
            session,
            tenant_id,
            room_type_id=body.room_type_id,
            name=body.name,
            status=body.status,
        )
    except RoomServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room.create",
        entity_type="room",
        entity_id=row.id,
        new_values=RoomRead.model_validate(row).model_dump(mode="json"),
    )
    return RoomRead.model_validate(row)


@router.patch("/{room_id}", response_model=RoomRead)
async def patch_room_by_id(
    room_id: UUID,
    _: RoomWriteRolesDep,
    body: RoomPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomRead:
    data = body.model_dump(exclude_unset=True)
    try:
        row = await patch_room(
            session,
            tenant_id,
            room_id,
            name=data.get("name"),
            status=data.get("status"),
            room_type_id=data.get("room_type_id"),
        )
    except RoomServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room.patch",
        entity_type="room",
        entity_id=room_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return RoomRead.model_validate(row)


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(
    room_id: UUID,
    _: RoomWriteRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    try:
        await soft_delete_room(session, tenant_id, room_id)
    except RoomServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room.delete",
        entity_type="room",
        entity_id=room_id,
        new_values={"soft_delete": True},
    )
