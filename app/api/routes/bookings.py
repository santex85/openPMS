"""Bookings REST API."""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    OptionalUserIdWriteDep,
    SessionDep,
    TenantIdDep,
    require_roles,
    require_scopes,
)
from app.api.routes.inventory import InventoryReadRolesDep, rooms_for_stay_query_params
from app.core.api_scopes import BOOKINGS_READ, BOOKINGS_WRITE
from app.core.rate_limit import limiter
from app.schemas.bookings import (
    BookingCreateRequest,
    BookingCreateResponse,
    BookingPatchRequest,
    BookingTapePage,
    BookingTapeRead,
    BookingUnpaidFolioSummaryRead,
)
from app.schemas.rooms import AssignableRoomsQueryParams, RoomRead
from app.schemas.folio import (
    BookingCheckoutBalanceWarning,
    FolioListResponse,
    FolioPostRequest,
    FolioTransactionRead,
)
from app.services.availability_lock import (
    InsufficientInventoryError,
    LedgerNotSeededError,
)
from app.services.booking_service import (
    AssignBookingRoomError,
    InvalidBookingContextError,
    PatchBookingError,
    create_booking,
    get_booking_tape,
    list_bookings_enriched,
    patch_booking,
)
from app.services.room_assignable_service import list_assignable_rooms_for_stay
from app.services.folio_service import (
    FolioError,
    add_folio_entry,
    list_folio_transactions,
    list_unpaid_folio_summary_for_property,
    reverse_folio_transaction,
)
from app.services.room_list_service import property_belongs_to_tenant
from app.services.pricing_service import MissingRatesError
from app.services.audit_service import record_audit
from app.services.channex_ari_triggers import schedule_push_channex_availability
from app.services.stay_dates import iter_stay_nights
from app.services.webhook_runner import (
    booking_quick_snapshot,
    emit_availability_for_dates,
    load_booking_for_webhook,
    run_booking_created_webhook,
    run_booking_patch_webhooks,
)


def _audit_patch_values(data: dict) -> dict:
    out: dict[str, object] = {}
    for k, v in data.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = str(v)
    return out


router = APIRouter()

BookingsReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "housekeeper", "receptionist")),
    Depends(require_scopes(BOOKINGS_READ)),
]
BookingsWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "receptionist")),
    Depends(require_scopes(BOOKINGS_WRITE)),
]


@router.get("", response_model=BookingTapePage)
@limiter.limit("60/minute")
async def get_bookings(
    request: Request,
    _: BookingsReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property to list bookings for"),
    start_date: date = Query(..., description="Inclusive window start (night date)"),
    end_date: date = Query(..., description="Inclusive window end (night date)"),
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by booking status",
    ),
    limit: int = Query(100, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
) -> BookingTapePage:
    role = getattr(request.state, "user_role", None)
    if role is not None and role.lower() == "housekeeper":
        today = datetime.now(UTC).date()
        if start_date != today or end_date != today:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Housekeeping role may only query today's bookings",
            )
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be on or before end_date",
        )
    items, total = await list_bookings_enriched(
        session,
        tenant_id,
        property_id=property_id,
        start_date=start_date,
        end_date=end_date,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return BookingTapePage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


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
async def get_bookings_assignable_rooms_for_stay(
    _: InventoryReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    params: Annotated[
        AssignableRoomsQueryParams,
        Depends(rooms_for_stay_query_params),
    ],
) -> list[RoomRead]:
    """Same as GET /inventory/rooms-for-stay; lives under /bookings so it ships with the tape API."""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</inventory/rooms-for-stay>; rel="successor-version"'
    rows = await list_assignable_rooms_for_stay(session, tenant_id, params)
    if rows is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property or room type not found",
        )
    return [RoomRead.model_validate(r) for r in rows]


@router.get(
    "/unpaid-folio-summary",
    response_model=list[BookingUnpaidFolioSummaryRead],
)
async def get_unpaid_folio_summary(
    _: BookingsReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property scope"),
) -> list[BookingUnpaidFolioSummaryRead]:
    """Bookings on this property with strictly positive folio balance (charges − payments)."""
    response.headers["Cache-Control"] = "private, no-store"
    if not await property_belongs_to_tenant(session, tenant_id, property_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property not found",
        )
    raw = await list_unpaid_folio_summary_for_property(
        session, tenant_id, property_id
    )
    out: list[BookingUnpaidFolioSummaryRead] = []
    for bid, bal, fn, ln in raw:
        name = f"{fn} {ln}".strip()
        out.append(
            BookingUnpaidFolioSummaryRead(
                booking_id=bid,
                balance=format(bal, "f"),
                guest_name=name if name else None,
            ),
        )
    return out


@router.get(
    "/{booking_id}",
    response_model=BookingTapeRead,
    summary="Get one booking by id",
    description=(
        "Returns the same shape as the bookings tape list (guest, stay dates, room). "
        "Use when the booking is outside the list window."
    ),
)
async def get_booking_by_id(
    _: BookingsReadRolesDep,
    booking_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> BookingTapeRead:
    row = await get_booking_tape(session, tenant_id, booking_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="booking not found"
        )
    return row


@router.get(
    "/{booking_id}/folio",
    response_model=FolioListResponse,
)
async def get_booking_folio(
    _: BookingsReadRolesDep,
    booking_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> FolioListResponse:
    try:
        rows, balance = await list_folio_transactions(
            session,
            tenant_id,
            booking_id,
        )
    except FolioError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    return FolioListResponse(
        transactions=[FolioTransactionRead.model_validate(r) for r in rows],
        balance=balance,
    )


@router.post(
    "/{booking_id}/folio",
    response_model=FolioTransactionRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_booking_folio(
    _: BookingsWriteRolesDep,
    booking_id: UUID,
    body: FolioPostRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
    user_id: OptionalUserIdWriteDep,
) -> FolioTransactionRead:
    try:
        tx = await add_folio_entry(
            session,
            tenant_id,
            booking_id,
            body,
            created_by=user_id,
        )
    except FolioError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    return FolioTransactionRead.model_validate(tx)


async def _reverse_folio_response(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    transaction_id: UUID,
    *,
    created_by: UUID | None,
) -> FolioTransactionRead:
    """Shared storno handler: inserts offsetting row; original row remains."""
    try:
        rev = await reverse_folio_transaction(
            session,
            tenant_id,
            booking_id,
            transaction_id,
            created_by=created_by,
        )
    except FolioError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    return FolioTransactionRead.model_validate(rev)


@router.post(
    "/{booking_id}/folio/{transaction_id}/reverse",
    status_code=status.HTTP_201_CREATED,
    response_model=FolioTransactionRead,
    summary="Reverse (storno) a folio transaction",
)
async def post_booking_folio_reverse(
    _: BookingsWriteRolesDep,
    booking_id: UUID,
    transaction_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
    user_id: OptionalUserIdWriteDep,
) -> FolioTransactionRead:
    """
    Storno: creates a new offsetting folio entry. The original transaction is not deleted.
    """
    return await _reverse_folio_response(
        session,
        tenant_id,
        booking_id,
        transaction_id,
        created_by=user_id,
    )


@router.delete(
    "/{booking_id}/folio/{transaction_id}",
    status_code=status.HTTP_200_OK,
    response_model=FolioTransactionRead,
    deprecated=True,
    summary="[Deprecated] Reverse folio via DELETE",
)
async def delete_booking_folio_transaction(
    _: BookingsWriteRolesDep,
    booking_id: UUID,
    transaction_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
    user_id: OptionalUserIdWriteDep,
) -> FolioTransactionRead:
    """
    **Deprecated** — use ``POST /bookings/{booking_id}/folio/{transaction_id}/reverse``
    (201 Created). This route remains as a backward-compatible alias and returns **200 OK**
    with the new reversal row (storno semantics: nothing is physically deleted).
    """
    return await _reverse_folio_response(
        session,
        tenant_id,
        booking_id,
        transaction_id,
        created_by=user_id,
    )


@router.patch(
    "/{booking_id}",
    responses={
        200: {"model": BookingCheckoutBalanceWarning},
        204: {"description": "Updated; no folio warning."},
    },
)
@limiter.limit("120/minute")
async def patch_booking_by_id(
    request: Request,
    background_tasks: BackgroundTasks,
    _: BookingsWriteRolesDep,
    booking_id: UUID,
    body: BookingPatchRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> Response:
    """
    Normally returns 204. When status changes to checked_out and folio balance is non-zero,
    returns 200 with folio_balance_warning and balance (guest debt if positive, overpay if negative).
    """
    patch_data = body.model_dump(exclude_unset=True)
    if not patch_data:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    b_before = await load_booking_for_webhook(session, tenant_id, booking_id)
    if b_before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="booking not found"
        )
    snap_before = booking_quick_snapshot(b_before)

    try:
        warn_balance = await patch_booking(session, tenant_id, booking_id, body)
    except AssignBookingRoomError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    except PatchBookingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    except InsufficientInventoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    b_after = await load_booking_for_webhook(session, tenant_id, booking_id)
    snap_after = booking_quick_snapshot(b_after) if b_after is not None else snap_before
    bs_c = str(snap_before.get("status") or "").strip().lower()
    as_c = str(snap_after.get("status") or "").strip().lower()
    folio_for_hook: str | None = None
    if as_c == "checked_out" and bs_c != "checked_out":
        folio_for_hook = (
            format(warn_balance, "f") if warn_balance is not None else "0.00"
        )

    factory = request.app.state.async_session_factory
    background_tasks.add_task(
        run_booking_patch_webhooks,
        factory,
        tenant_id,
        booking_id,
        before=snap_before,
        after=snap_after,
        cancellation_reason=body.cancellation_reason,
        folio_balance_on_checkout=folio_for_hook,
    )

    avail_touch = False
    if {"check_in", "check_out"} & patch_data.keys():
        avail_touch = True
    st = patch_data.get("status")
    if st is not None and st.strip().lower() in ("cancelled", "no_show"):
        avail_touch = True
    if avail_touch and b_before is not None and b_after is not None:
        rb = {ln.room_type_id for ln in b_before.lines}
        ra = {ln.room_type_id for ln in b_after.lines}
        if len(rb) == 1 and rb == ra:
            rt_id = next(iter(rb))
            all_dates = sorted(
                {ln.date for ln in b_before.lines} | {ln.date for ln in b_after.lines},
            )
            background_tasks.add_task(
                emit_availability_for_dates,
                factory,
                tenant_id,
                rt_id,
                all_dates,
            )
            schedule_push_channex_availability(
                background_tasks,
                tenant_id,
                b_after.property_id,
                rt_id,
                all_dates,
            )

    await record_audit(
        session,
        tenant_id=tenant_id,
        action="booking.patch",
        entity_type="booking",
        entity_id=booking_id,
        new_values=_audit_patch_values(patch_data),
    )
    await session.commit()
    if warn_balance is not None:
        payload = BookingCheckoutBalanceWarning(balance=warn_balance).model_dump(
            mode="json",
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "",
    response_model=BookingCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")
async def post_booking(
    request: Request,
    background_tasks: BackgroundTasks,
    _: BookingsWriteRolesDep,
    body: BookingCreateRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> BookingCreateResponse:
    try:
        out = await create_booking(session, tenant_id, body)
    except InsufficientInventoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except LedgerNotSeededError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except MissingRatesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"missing_dates": [d.isoformat() for d in exc.missing_dates]},
        ) from exc
    except InvalidBookingContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="booking.create",
        entity_type="booking",
        entity_id=out.booking_id,
        new_values={
            "property_id": str(body.property_id),
            "check_in": body.check_in.isoformat(),
            "check_out": body.check_out.isoformat(),
            "status": body.status,
        },
    )
    factory = request.app.state.async_session_factory
    background_tasks.add_task(
        run_booking_created_webhook,
        factory,
        tenant_id,
        out.booking_id,
    )
    stay_nights = iter_stay_nights(body.check_in, body.check_out)
    schedule_push_channex_availability(
        background_tasks,
        tenant_id,
        body.property_id,
        body.room_type_id,
        stay_nights,
    )
    return out
