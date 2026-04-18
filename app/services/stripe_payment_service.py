"""Stripe Connect payments: saved PMs, charges, refunds (Phase 3)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.stripe_secrets import decrypt_stripe_account_id
from app.models.billing.stripe_charge import StripeCharge
from app.models.billing.stripe_connection import StripeConnection
from app.models.billing.stripe_payment_method import StripePaymentMethod
from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.core.property import Property


class StripePaymentError(Exception):
    """Business / validation error for Stripe payment flows."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _require_stripe_secret(settings: Settings) -> None:
    if not (settings.stripe_secret_key or "").strip():
        raise StripePaymentError("Stripe secret key is not configured", status_code=503)


def _money_to_stripe_cents(amount: Decimal) -> int:
    cents = (amount * Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(cents)


async def _require_booking(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Booking:
    booking = await session.scalar(
        select(Booking).where(
            Booking.tenant_id == tenant_id,
            Booking.id == booking_id,
        ),
    )
    if booking is None:
        raise StripePaymentError("booking not found", status_code=404)
    return booking


async def _active_connection_and_account(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> tuple[StripeConnection, str]:
    row = await session.scalar(
        select(StripeConnection).where(
            StripeConnection.tenant_id == tenant_id,
            StripeConnection.property_id == property_id,
            StripeConnection.disconnected_at.is_(None),
        ),
    )
    if row is None:
        raise StripePaymentError(
            "Stripe is not connected for this property", status_code=422
        )
    plain = decrypt_stripe_account_id(settings, row.stripe_account_id)
    return row, plain


async def save_payment_method(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    stripe_pm_id: str,
    booking_id: UUID | None = None,
    label: str | None = None,
) -> StripePaymentMethod:
    _require_stripe_secret(settings)
    _, acct = await _active_connection_and_account(
        settings, session, tenant_id, property_id
    )
    if booking_id is not None:
        booking = await _require_booking(session, tenant_id, booking_id)
        if booking.property_id != property_id:
            raise StripePaymentError(
                "booking does not belong to this property",
                status_code=422,
            )
    try:
        pm_obj = stripe.PaymentMethod.retrieve(
            stripe_pm_id,
            api_key=settings.stripe_secret_key.strip(),
            stripe_account=acct,
        )
    except stripe.StripeError as exc:
        raise StripePaymentError(
            getattr(exc, "user_message", None) or str(exc),
            status_code=422,
        ) from exc

    last4: str | None = None
    brand: str | None = None
    exp_m: int | None = None
    exp_y: int | None = None
    card = getattr(pm_obj, "card", None)
    if card is not None:
        last4 = getattr(card, "last4", None)
        brand = getattr(card, "brand", None)
        exp_m = getattr(card, "exp_month", None)
        exp_y = getattr(card, "exp_year", None)

    row = StripePaymentMethod(
        tenant_id=tenant_id,
        property_id=property_id,
        booking_id=booking_id,
        stripe_pm_id=stripe_pm_id.strip(),
        card_last4=last4,
        card_brand=brand,
        card_exp_month=int(exp_m) if exp_m is not None else None,
        card_exp_year=int(exp_y) if exp_y is not None else None,
        label=label.strip() if label and label.strip() else None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def list_payment_methods(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    booking_id: UUID | None = None,
) -> list[StripePaymentMethod]:
    stmt = select(StripePaymentMethod).where(
        StripePaymentMethod.tenant_id == tenant_id,
        StripePaymentMethod.property_id == property_id,
    )
    if booking_id is not None:
        stmt = stmt.where(StripePaymentMethod.booking_id == booking_id)
    stmt = stmt.order_by(StripePaymentMethod.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_payment_method(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    pm_row_id: UUID,
) -> None:
    _require_stripe_secret(settings)
    row = await session.scalar(
        select(StripePaymentMethod).where(
            StripePaymentMethod.tenant_id == tenant_id,
            StripePaymentMethod.id == pm_row_id,
        ),
    )
    if row is None:
        raise StripePaymentError("payment method not found", status_code=404)
    _, acct = await _active_connection_and_account(
        settings,
        session,
        tenant_id,
        row.property_id,
    )
    try:
        stripe.PaymentMethod.detach(
            row.stripe_pm_id,
            api_key=settings.stripe_secret_key.strip(),
            stripe_account=acct,
        )
    except stripe.StripeError as exc:
        raise StripePaymentError(
            getattr(exc, "user_message", None) or str(exc),
            status_code=422,
        ) from exc
    await session.delete(row)
    await session.flush()


async def charge_booking(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    stripe_pm_row_id: UUID,
    amount: Decimal,
    *,
    label: str | None = None,
) -> StripeCharge:
    _require_stripe_secret(settings)
    if amount <= 0:
        raise StripePaymentError("amount must be positive", status_code=422)

    booking = await _require_booking(session, tenant_id, booking_id)
    pm_row = await session.scalar(
        select(StripePaymentMethod).where(
            StripePaymentMethod.tenant_id == tenant_id,
            StripePaymentMethod.id == stripe_pm_row_id,
        ),
    )
    if pm_row is None:
        raise StripePaymentError("saved payment method not found", status_code=422)
    if pm_row.property_id != booking.property_id:
        raise StripePaymentError(
            "payment method does not belong to this booking's property",
            status_code=422,
        )
    if pm_row.booking_id is not None and pm_row.booking_id != booking_id:
        raise StripePaymentError(
            "payment method is restricted to another booking",
            status_code=422,
        )

    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == booking.property_id,
        ),
    )
    if prop is None:
        raise StripePaymentError("property not found", status_code=404)

    _, acct = await _active_connection_and_account(
        settings,
        session,
        tenant_id,
        booking.property_id,
    )

    currency = prop.currency.strip().lower()
    amount_cents = _money_to_stripe_cents(amount.quantize(Decimal("0.01")))

    try:
        pi = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            payment_method=pm_row.stripe_pm_id,
            confirm=True,
            on_behalf_of=acct,
            stripe_account=acct,
            api_key=settings.stripe_secret_key.strip(),
        )
    except stripe.StripeError as exc:
        msg = getattr(exc, "user_message", None) or str(exc)
        failed_id = f"failed_{uuid4()}"
        fail_row = StripeCharge(
            tenant_id=tenant_id,
            property_id=booking.property_id,
            booking_id=booking_id,
            folio_tx_id=None,
            stripe_charge_id=failed_id,
            stripe_pm_id=pm_row.stripe_pm_id,
            amount=amount.quantize(Decimal("0.01")),
            currency=currency.upper(),
            status="failed",
            failure_message=msg,
        )
        session.add(fail_row)
        await session.flush()
        # Persist failed charge before raising: get_db rolls back the session on any
        # exception from the route, which would otherwise drop this audit row.
        await session.commit()
        raise StripePaymentError(msg, status_code=422) from exc

    pi_id = str(getattr(pi, "id", "") or "")
    if not pi_id:
        raise StripePaymentError(
            "Stripe did not return a payment intent id", status_code=502
        )

    folio = FolioTransaction(
        tenant_id=tenant_id,
        booking_id=booking_id,
        transaction_type="Payment",
        amount=amount.quantize(Decimal("0.01")),
        payment_method="stripe",
        description=label.strip() if label and label.strip() else "Stripe card payment",
        created_by=None,
        category="payment",
        source_channel="stripe",
    )
    session.add(folio)
    await session.flush()

    ch = StripeCharge(
        tenant_id=tenant_id,
        property_id=booking.property_id,
        booking_id=booking_id,
        folio_tx_id=folio.id,
        stripe_charge_id=pi_id,
        stripe_pm_id=pm_row.stripe_pm_id,
        amount=amount.quantize(Decimal("0.01")),
        currency=currency.upper(),
        status="succeeded",
        failure_message=None,
    )
    session.add(ch)
    await session.flush()
    await session.refresh(ch)
    return ch


async def refund_stripe_charge(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    stripe_charge_row_id: UUID,
    amount: Decimal | None = None,
) -> StripeCharge:
    _require_stripe_secret(settings)
    await _require_booking(session, tenant_id, booking_id)

    ch = await session.scalar(
        select(StripeCharge).where(
            StripeCharge.tenant_id == tenant_id,
            StripeCharge.id == stripe_charge_row_id,
        ),
    )
    if ch is None:
        raise StripePaymentError("charge not found", status_code=404)
    if ch.booking_id != booking_id:
        raise StripePaymentError(
            "charge does not belong to this booking", status_code=404
        )
    if ch.status == "failed":
        raise StripePaymentError("cannot refund a failed charge", status_code=422)
    if ch.status == "refunded":
        raise StripePaymentError("charge is already fully refunded", status_code=422)
    if ch.status == "partial_refund":
        raise StripePaymentError(
            "only one refund is allowed per charge", status_code=422
        )

    refund_amt = ch.amount if amount is None else amount.quantize(Decimal("0.01"))
    if amount is not None and refund_amt <= 0:
        raise StripePaymentError("refund amount must be positive", status_code=422)
    if refund_amt > ch.amount:
        raise StripePaymentError("refund amount exceeds charge amount", status_code=422)

    _, acct = await _active_connection_and_account(
        settings,
        session,
        tenant_id,
        ch.property_id,
    )

    refund_cents = _money_to_stripe_cents(refund_amt)
    try:
        stripe.Refund.create(
            payment_intent=ch.stripe_charge_id,
            amount=refund_cents,
            api_key=settings.stripe_secret_key.strip(),
            stripe_account=acct,
        )
    except stripe.StripeError as exc:
        raise StripePaymentError(
            getattr(exc, "user_message", None) or str(exc),
            status_code=422,
        ) from exc

    new_status = "refunded" if refund_amt >= ch.amount else "partial_refund"
    ch.status = new_status
    await session.flush()

    folio = FolioTransaction(
        tenant_id=tenant_id,
        booking_id=booking_id,
        transaction_type="Payment",
        amount=(-refund_amt).quantize(Decimal("0.01")),
        payment_method="stripe",
        description="Stripe refund",
        created_by=None,
        category="payment",
        source_channel="stripe",
    )
    session.add(folio)
    await session.flush()
    await session.refresh(ch)
    return ch


async def list_booking_stripe_charges(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> list[StripeCharge]:
    await _require_booking(session, tenant_id, booking_id)
    stmt = (
        select(StripeCharge)
        .where(
            StripeCharge.tenant_id == tenant_id,
            StripeCharge.booking_id == booking_id,
        )
        .order_by(StripeCharge.created_at.asc(), StripeCharge.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
