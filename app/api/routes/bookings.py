"""Bookings REST API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantIdDep, get_db
from app.schemas.bookings import (
    BookingCreateRequest,
    BookingCreateResponse,
    BookingRead,
)
from app.services.availability_lock import (
    InsufficientInventoryError,
    LedgerNotSeededError,
)
from app.services.booking_service import (
    InvalidBookingContextError,
    create_booking,
    list_bookings,
)
from app.services.pricing_service import MissingRatesError

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[BookingRead])
async def get_bookings(
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[BookingRead]:
    rows = await list_bookings(session, tenant_id)
    return [BookingRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=BookingCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_booking(
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
