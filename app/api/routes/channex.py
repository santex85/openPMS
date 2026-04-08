"""Channex Channel Manager onboarding (JWT + scopes)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import SessionDep, TenantIdDep, require_jwt_user, require_roles, require_scopes
from app.core.api_scopes import CHANNEX_READ, CHANNEX_WRITE
from app.schemas.channex import (
    ChannexConnectRequest,
    ChannexPropertyLinkRead,
    ChannexPropertyRead,
    ChannexRatePlanRead,
    ChannexRoomTypeRead,
    ChannexStatusRead,
    ChannexValidateKeyRequest,
    RateMappingRequest,
    RoomMappingRequest,
)
from app.services.audit_service import record_audit
from app.services import channex_service
from app.services.channex_service import ChannexServiceError

router = APIRouter()

ChannexReadDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(CHANNEX_READ)),
]
ChannexWriteDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(CHANNEX_WRITE)),
]


def _http_from_service(exc: ChannexServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/validate-key",
    response_model=list[ChannexPropertyRead],
    summary="Validate Channex API key and list properties",
)
async def validate_channex_key(
    _: ChannexWriteDep,
    body: ChannexValidateKeyRequest,
) -> list[ChannexPropertyRead]:
    try:
        props = await channex_service.validate_key(body.api_key, body.env)
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    return [ChannexPropertyRead(id=p.id, title=p.title) for p in props]


@router.post(
    "/connect",
    response_model=ChannexPropertyLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Connect Channex account to an OpenPMS property",
)
async def connect_channex(
    _: ChannexWriteDep,
    body: ChannexConnectRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> ChannexPropertyLinkRead:
    try:
        row = await channex_service.connect(
            session,
            tenant_id,
            property_id,
            body.api_key,
            body.env,
            body.channex_property_id,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.connect",
        entity_type="channex_property_link",
        entity_id=row.id,
        new_values={
            "property_id": str(property_id),
            "channex_property_id": row.channex_property_id,
            "channex_env": row.channex_env,
        },
    )
    return ChannexPropertyLinkRead.model_validate(row)


@router.get(
    "/status",
    response_model=ChannexStatusRead,
    summary="Channex connection status for a property",
)
async def channex_status(
    _: ChannexReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> ChannexStatusRead:
    return await channex_service.get_status(session, tenant_id, property_id)


@router.get(
    "/channex-rooms",
    response_model=list[ChannexRoomTypeRead],
    summary="Room types from Channex for the linked property",
)
async def channex_rooms(
    _: ChannexReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> list[ChannexRoomTypeRead]:
    try:
        return await channex_service.get_channex_rooms(session, tenant_id, property_id)
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc


@router.get(
    "/channex-rates",
    response_model=list[ChannexRatePlanRead],
    summary="Rate plans from Channex for the linked property",
)
async def channex_rates(
    _: ChannexReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> list[ChannexRatePlanRead]:
    try:
        return await channex_service.get_channex_rates(session, tenant_id, property_id)
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc


@router.post(
    "/map-rooms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Save OpenPMS room type ↔ Channex room type mappings",
)
async def map_channex_rooms(
    request: Request,
    _: ChannexWriteDep,
    body: RoomMappingRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> None:
    _ = request
    try:
        await channex_service.save_room_mappings(
            session,
            tenant_id,
            property_id,
            body.mappings,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.map_rooms",
        entity_type="property",
        entity_id=property_id,
        new_values={"mappings_count": len(body.mappings)},
    )


@router.post(
    "/map-rates",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Save OpenPMS rate plan ↔ Channex rate plan mappings",
)
async def map_channex_rates(
    request: Request,
    _: ChannexWriteDep,
    body: RateMappingRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> None:
    _ = request
    try:
        await channex_service.save_rate_mappings(
            session,
            tenant_id,
            property_id,
            body.mappings,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.map_rates",
        entity_type="property",
        entity_id=property_id,
        new_values={"mappings_count": len(body.mappings)},
    )


@router.post(
    "/activate",
    response_model=ChannexPropertyLinkRead,
    summary="Mark Channex integration active (no initial sync yet)",
)
async def activate_channex(
    request: Request,
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> ChannexPropertyLinkRead:
    _ = request
    try:
        row = await channex_service.activate(session, tenant_id, property_id)
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.activate",
        entity_type="channex_property_link",
        entity_id=row.id,
        new_values={"status": row.status},
    )
    return ChannexPropertyLinkRead.model_validate(row)


@router.post(
    "/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Channex connection and all mappings for a property",
)
async def disconnect_channex(
    request: Request,
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> None:
    _ = request
    await channex_service.disconnect(session, tenant_id, property_id)
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.disconnect",
        entity_type="property",
        entity_id=property_id,
        new_values={},
    )
