"""Assemble booking receipt payload including optional property tax."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.billing.tax_config import TaxMode
from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.schemas.booking_receipt import BookingReceiptRead
from app.services.folio_service import FolioError, sum_booking_charge_amounts
from app.services.tax_service import (
    calculate_property_tax,
    get_tax_config,
    property_tax_summary_lines,
)


async def build_booking_receipt(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> BookingReceiptRead:
    stmt = (
        select(Booking)
        .options(selectinload(Booking.guest))
        .where(Booking.tenant_id == tenant_id, Booking.id == booking_id)
    )
    booking = await session.scalar(stmt)
    if booking is None:
        raise FolioError("booking not found", status_code=404)

    guest: Guest | None = booking.guest
    charge_total = await sum_booking_charge_amounts(session, tenant_id, booking_id)

    prop_row = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == booking.property_id,
        ),
    )
    currency = prop_row.currency if prop_row is not None else None

    base = BookingReceiptRead(
        booking_id=booking.id,
        property_id=booking.property_id,
        guest_first_name=guest.first_name if guest else None,
        guest_last_name=guest.last_name if guest else None,
        currency=currency,
        charge_subtotal=format(charge_total, "f"),
    )

    cfg = await get_tax_config(session, tenant_id, booking.property_id)
    if cfg is None or cfg.tax_mode == TaxMode.off:
        return base

    breakdown = calculate_property_tax(charge_total, cfg)
    lines = property_tax_summary_lines(cfg, breakdown)
    return base.model_copy(
        update={
            "tax_mode": cfg.tax_mode.value,
            "tax_name": cfg.tax_name,
            "tax_rate": format(cfg.tax_rate, "f"),
            "tax_breakdown": breakdown,
            "tax_summary_lines": lines,
        },
    )
