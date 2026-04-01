"""Room types REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import ROOM_TYPES_READ, ROOM_TYPES_WRITE
from app.schemas.room_type import RoomTypeCreate, RoomTypeRead
from app.services import room_type_service
from app.services.audit_service import record_audit

router = APIRouter()

RoomTypeReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(ROOM_TYPES_READ)),
]
RoomTypeWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(ROOM_TYPES_WRITE)),
]


@router.post(
    "",
    response_model=RoomTypeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_room_type(
    _: RoomTypeWriteRolesDep,
    body: RoomTypeCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomTypeRead:
    try:
        rt = await room_type_service.create_room_type(session, tenant_id, body)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        ) from None
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room_type.create",
        entity_type="room_type",
        entity_id=rt.id,
        new_values=RoomTypeRead.model_validate(rt).model_dump(mode="json"),
    )
    return RoomTypeRead.model_validate(rt)


@router.get("", response_model=list[RoomTypeRead])
async def list_room_types(
    _: RoomTypeReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID | None, Query()] = None,
) -> list[RoomTypeRead]:
    rows = await room_type_service.list_room_types(
        session,
        tenant_id,
        property_id=property_id,
    )
    return [RoomTypeRead.model_validate(r) for r in rows]


@router.get("/{room_type_id}", response_model=RoomTypeRead)
async def get_room_type(
    _: RoomTypeReadRolesDep,
    room_type_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomTypeRead:
    rt = await room_type_service.get_room_type(session, tenant_id, room_type_id)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room type not found",
        )
    return RoomTypeRead.model_validate(rt)
