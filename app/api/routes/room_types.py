"""Room types REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import ROOM_TYPES_READ, ROOM_TYPES_WRITE
from app.schemas.room_type import RoomTypeCreate, RoomTypePatch, RoomTypeRead
from app.services import room_type_service
from app.services.audit_service import record_audit
from app.core.rate_limit import limiter

router = APIRouter()

RoomTypeReadRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager", "viewer", "receptionist"),
            require_scopes(ROOM_TYPES_READ),
        ),
    ),
]
RoomTypeWriteRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(ROOM_TYPES_WRITE),
        ),
    ),
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


@router.patch("/{room_type_id}", response_model=RoomTypeRead)
@limiter.limit("120/minute")
async def patch_room_type(
    request: Request,
    _: RoomTypeWriteRolesDep,
    room_type_id: UUID,
    body: RoomTypePatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> RoomTypeRead:
    _ = request
    try:
        rt = await room_type_service.patch_room_type(
            session, tenant_id, room_type_id, body
        )
    except ValueError as exc:
        detail = str(exc)
        if "max_occupancy" in detail:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room type not found",
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room_type.patch",
        entity_type="room_type",
        entity_id=room_type_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return RoomTypeRead.model_validate(rt)


@router.delete("/{room_type_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("120/minute")
async def delete_room_type(
    request: Request,
    _: RoomTypeWriteRolesDep,
    room_type_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    _ = request
    try:
        await room_type_service.soft_delete_room_type(
            session, tenant_id, room_type_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room type not found",
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="room_type.delete",
        entity_type="room_type",
        entity_id=room_type_id,
        new_values={},
    )
