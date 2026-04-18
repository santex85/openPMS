"""Stripe Payments: saved PMs, charge, refund, charge history (Phase 3)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.responses import Response

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import (
    BOOKINGS_READ,
    BOOKINGS_WRITE,
    PROPERTIES_READ,
    PROPERTIES_WRITE,
)
from app.core.config import get_settings
from app.schemas.stripe_payments import (
    ChargeRead,
    ChargeRequest,
    PaymentMethodRead,
    RefundRequest,
    SavePaymentMethodRequest,
)
from app.services import property_service
from app.services.stripe_payment_service import (
    StripePaymentError,
    charge_booking,
    delete_payment_method,
    list_booking_stripe_charges,
    list_payment_methods,
    refund_stripe_charge,
    save_payment_method,
)

router = APIRouter()

StripePmOwnerManagerReadDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(PROPERTIES_READ),
        ),
    ),
]
StripePmOwnerManagerWriteDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(PROPERTIES_WRITE),
        ),
    ),
]
StripeBookingChargeDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(BOOKINGS_WRITE),
        ),
    ),
]
StripeBookingReadChargesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(BOOKINGS_READ),
        ),
    ),
]
StripeRefundOwnerDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner"),
            require_scopes(BOOKINGS_WRITE),
        ),
    ),
]


def _payment_http_error(exc: StripePaymentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/properties/{property_id}/stripe/payment-methods",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_stripe_payment_method(
    _: StripePmOwnerManagerWriteDep,
    property_id: UUID,
    body: SavePaymentMethodRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> PaymentMethodRead:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    settings = get_settings()
    try:
        row = await save_payment_method(
            settings,
            session,
            tenant_id,
            property_id,
            body.stripe_pm_id.strip(),
            booking_id=body.booking_id,
            label=body.label,
        )
    except StripePaymentError as exc:
        raise _payment_http_error(exc) from exc
    return PaymentMethodRead.model_validate(row)


@router.get(
    "/properties/{property_id}/stripe/payment-methods",
    response_model=list[PaymentMethodRead],
)
async def get_stripe_payment_methods(
    _: StripePmOwnerManagerReadDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
    booking_id: UUID | None = Query(None, description="Filter by linked booking"),
) -> list[PaymentMethodRead]:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    rows = await list_payment_methods(
        session, tenant_id, property_id, booking_id=booking_id
    )
    return [PaymentMethodRead.model_validate(r) for r in rows]


@router.delete(
    "/stripe/payment-methods/{pm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_stripe_payment_method_route(
    _: StripePmOwnerManagerWriteDep,
    pm_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> Response:
    settings = get_settings()
    try:
        await delete_payment_method(settings, session, tenant_id, pm_id)
    except StripePaymentError as exc:
        raise _payment_http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bookings/{booking_id}/stripe/charge",
    response_model=ChargeRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_booking_stripe_charge(
    _: StripeBookingChargeDep,
    booking_id: UUID,
    body: ChargeRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> ChargeRead:
    settings = get_settings()
    try:
        row = await charge_booking(
            settings,
            session,
            tenant_id,
            booking_id,
            body.stripe_pm_id,
            body.amount,
            label=body.label,
        )
    except StripePaymentError as exc:
        raise _payment_http_error(exc) from exc
    return ChargeRead.model_validate(row)


@router.post(
    "/bookings/{booking_id}/stripe/refund",
    response_model=ChargeRead,
)
async def post_booking_stripe_refund(
    _: StripeRefundOwnerDep,
    booking_id: UUID,
    body: RefundRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> ChargeRead:
    settings = get_settings()
    try:
        row = await refund_stripe_charge(
            settings,
            session,
            tenant_id,
            booking_id,
            body.stripe_charge_id,
            amount=body.amount,
        )
    except StripePaymentError as exc:
        raise _payment_http_error(exc) from exc
    return ChargeRead.model_validate(row)


@router.get(
    "/bookings/{booking_id}/stripe/charges",
    response_model=list[ChargeRead],
)
async def get_booking_stripe_charges(
    _: StripeBookingReadChargesDep,
    booking_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[ChargeRead]:
    try:
        rows = await list_booking_stripe_charges(session, tenant_id, booking_id)
    except StripePaymentError as exc:
        raise _payment_http_error(exc) from exc
    return [ChargeRead.model_validate(r) for r in rows]
