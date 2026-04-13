"""Channex Channel Manager onboarding (JWT + scopes)."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from sqlalchemy import select

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_jwt_user,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import CHANNEX_READ, CHANNEX_WRITE
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.schemas.channex import (
    ChannexConnectRequest,
    ChannexPropertyLinkRead,
    ChannexPropertyRead,
    ChannexProvisionRead,
    ChannexRatePlanRead,
    ChannexRevisionFailedRead,
    ChannexRevisionRetryQueuedResponse,
    ChannexRevisionsFailedListResponse,
    ChannexRoomTypeRead,
    ChannexStatusRead,
    ChannexSyncQueuedResponse,
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
    Depends(
        chain_dependency_runners(
            require_jwt_user(),
            require_roles("owner", "manager"),
            require_scopes(CHANNEX_READ),
        ),
    ),
]
ChannexWriteDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_jwt_user(),
            require_roles("owner", "manager"),
            require_scopes(CHANNEX_WRITE),
        ),
    ),
]


def _http_from_service(exc: ChannexServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _enqueue_channex_ari_sync_job(tenant_id: UUID, property_id: UUID) -> None:
    from app.tasks.channex_ari_sync import channex_full_ari_sync

    channex_full_ari_sync.delay(str(tenant_id), str(property_id))


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
    "/create-property",
    response_model=ChannexPropertyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Channex property from the current OpenPMS hotel",
)
async def create_channex_property_endpoint(
    _: ChannexWriteDep,
    body: ChannexValidateKeyRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> ChannexPropertyRead:
    try:
        created = await channex_service.create_channex_property_from_openpms(
            session,
            tenant_id,
            property_id,
            body.api_key,
            body.env,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.create_property",
        entity_type="property",
        entity_id=property_id,
        new_values={"channex_property_id": created.id},
    )
    return ChannexPropertyRead(id=created.id, title=created.title)


@router.post(
    "/provision-from-openpms",
    response_model=ChannexProvisionRead,
    summary="Create missing Channex room types and rate plans from OpenPMS data",
)
async def provision_channex_from_openpms_endpoint(
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
) -> ChannexProvisionRead:
    try:
        result = await channex_service.provision_channex_from_openpms(
            session,
            tenant_id,
            property_id,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.provision_from_openpms",
        entity_type="property",
        entity_id=property_id,
        new_values={
            "room_types_created": result.room_types_created,
            "room_types_skipped": result.room_types_skipped,
            "rate_plans_created": result.rate_plans_created,
            "rate_plans_skipped": result.rate_plans_skipped,
        },
    )
    return result


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
    "/revisions/failed",
    response_model=ChannexRevisionsFailedListResponse,
    summary="List Channex booking revisions that failed ingestion (error state)",
)
async def list_failed_channex_revisions(
    _: ChannexReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[
        UUID | None,
        Query(description="Filter by OpenPMS property UUID (via Channex link)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChannexRevisionsFailedListResponse:
    rows, total = await channex_service.list_failed_channex_booking_revisions(
        session,
        tenant_id,
        property_id=property_id,
        limit=limit,
        offset=offset,
    )
    items = [
        ChannexRevisionFailedRead(
            id=rev.id,
            channex_revision_id=rev.channex_revision_id,
            channex_booking_id=rev.channex_booking_id,
            property_id=prop_id,
            channel_code=rev.channel_code,
            error_message=rev.error_message,
            received_at=rev.received_at,
            processed_at=rev.processed_at,
        )
        for rev, prop_id in rows
    ]
    return ChannexRevisionsFailedListResponse(total=total, items=items)


@router.post(
    "/revisions/{revision_id}/retry",
    response_model=ChannexRevisionRetryQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue async retry of a failed Channex booking revision (replay stored payload)",
)
async def retry_failed_channex_revision(
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    revision_id: Annotated[
        UUID,
        Path(
            description=(
                "OpenPMS ``channex_booking_revisions.id`` (primary key). "
                "Not the Channex-side revision id."
            ),
        ),
    ],
) -> ChannexRevisionRetryQueuedResponse:
    rev = await session.scalar(
        select(ChannexBookingRevision).where(
            ChannexBookingRevision.id == revision_id,
            ChannexBookingRevision.tenant_id == tenant_id,
        ),
    )
    if rev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking revision not found",
        )
    if rev.processing_status != "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only revisions in error state can be retried",
        )
    from app.tasks.channex_booking_retry import channex_retry_booking_revision

    channex_retry_booking_revision.delay(str(revision_id), str(tenant_id))
    return ChannexRevisionRetryQueuedResponse()


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
    summary=(
        "Activate Channex: register webhook (if CHANNEX_WEBHOOK_URL set), "
        "enqueue full ARI sync (365 days)"
    ),
)
async def activate_channex(
    request: Request,
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(_enqueue_channex_ari_sync_job, tenant_id, property_id)
    return ChannexPropertyLinkRead.model_validate(row)


@router.post(
    "/sync",
    response_model=ChannexSyncQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue full ARI sync to Channex (365 days) for an active link",
)
async def sync_channex(
    request: Request,
    _: ChannexWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: Annotated[UUID, Query(description="OpenPMS property UUID")],
    background_tasks: BackgroundTasks,
) -> ChannexSyncQueuedResponse:
    _ = request
    try:
        row = await channex_service.require_active_channex_link(
            session,
            tenant_id,
            property_id,
        )
    except ChannexServiceError as exc:
        raise _http_from_service(exc) from exc
    _ = row
    background_tasks.add_task(_enqueue_channex_ari_sync_job, tenant_id, property_id)
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="channex.sync",
        entity_type="property",
        entity_id=property_id,
        new_values={},
    )
    return ChannexSyncQueuedResponse()


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
