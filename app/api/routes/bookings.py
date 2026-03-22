"""Bookings API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantIdDep, get_db
from app.schemas.bookings import BookingCreateRequest, BookingCreateResponse
from app.services.availability_lock import (
    InsufficientInventoryError,
    LedgerNotSeededError,
)
from app.services.booking_service import InvalidBookingContextError, create_booking
from app.services.pricing_service import MissingRatesError

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "",
    response_model=BookingCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_booking_endpoint(
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: BookingCreateRequest,
) -> BookingCreateResponse:
    try:
        return await create_booking(session, tenant_id, body)
    except MissingRatesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "missing_rates",
                "missing_dates": [d.isoformat() for d in exc.missing_dates],
            },
        ) from exc
    except LedgerNotSeededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except InsufficientInventoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except InvalidBookingContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
