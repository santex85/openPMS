"""Bookings REST API."""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import SessionDep, TenantIdDep, require_roles
from app.schemas.bookings import (
    BookingCreateRequest,
    BookingCreateResponse,
    BookingPatchRequest,
    BookingTapeRead,
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
    list_bookings_enriched,
    patch_booking,
)
from app.services.pricing_service import MissingRatesError

router = APIRouter()

BookingsReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "housekeeper", "receptionist")),
]
BookingsWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "receptionist")),
]


@router.get("", response_model=list[BookingTapeRead])
async def get_bookings(
    request: Request,
    _: BookingsReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property to list bookings for"),
    start_date: date = Query(..., description="Inclusive window start (night date)"),
    end_date: date = Query(..., description="Inclusive window end (night date)"),
    status: str | None = Query(None, description="Filter by booking status"),
) -> list[BookingTapeRead]:
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
    return await list_bookings_enriched(
        session,
        tenant_id,
        property_id=property_id,
        start_date=start_date,
        end_date=end_date,
        status_filter=status,
    )


@router.patch("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_booking_by_id(
    _: BookingsWriteRolesDep,
    booking_id: UUID,
    body: BookingPatchRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    try:
        await patch_booking(session, tenant_id, booking_id, body)
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


@router.post(
    "",
    response_model=BookingCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_booking(
    _: BookingsWriteRolesDep,
    body: BookingCreateRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> BookingCreateResponse:
    try:
        return await create_booking(session, tenant_id, body)
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

