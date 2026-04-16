"""Generate booking invoice PDF (HTML + WeasyPrint)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence

from app.integrations.resend.renderer import render_email
from app.models.billing.tax_config import TaxConfig, TaxMode
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.services.tax_service import calculate_property_tax, property_tax_summary_lines


def _money(amount: Decimal, currency_code: str) -> str:
    return f"{format(amount.quantize(Decimal('0.01')), 'f')} {currency_code}"


def _folio_totals(
    folio_lines: Sequence[FolioTransaction],
) -> tuple[Decimal, Decimal, Decimal]:
    charges = Decimal("0")
    payments = Decimal("0")
    for t in folio_lines:
        if t.transaction_type == "Charge":
            charges += t.amount
        elif t.transaction_type == "Payment":
            payments += t.amount
    charges = charges.quantize(Decimal("0.01"))
    payments = payments.quantize(Decimal("0.01"))
    balance = (charges - payments).quantize(Decimal("0.01"))
    return charges, payments, balance


def _stay_dates(booking_lines: Sequence[BookingLine] | None) -> tuple[str, str]:
    if booking_lines is None or len(booking_lines) == 0:
        return "", ""
    dates = [ln.date for ln in booking_lines]
    ci = min(dates)
    co = max(dates) + timedelta(days=1)
    return ci.isoformat(), co.isoformat()


def build_invoice_pdf_context(
    booking: Booking,
    property_: Property,
    folio_lines: Sequence[FolioTransaction],
    tax_config: TaxConfig | None,
    *,
    guest: Guest | None = None,
    booking_lines: Sequence[BookingLine] | None = None,
    room_name: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Build a primitive-only Jinja context for ``invoice.html`` (PDF layout).

    Tax base matches receipts: sum of folio **Charge** rows (after discounts).
    """
    g = guest
    if g is None:
        g = getattr(booking, "guest", None)

    currency = property_.currency
    gen_at = generated_at or datetime.now(timezone.utc)
    if gen_at.tzinfo is None:
        gen_at = gen_at.replace(tzinfo=timezone.utc)
    gen_str = gen_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    guest_display = "—"
    if g is not None:
        guest_display = f"{g.first_name} {g.last_name}".strip() or "—"

    check_in, check_out = _stay_dates(booking_lines)

    sorted_lines = sorted(
        folio_lines,
        key=lambda t: (t.created_at, t.id),
    )
    folio_rows: list[dict[str, str]] = []
    for t in sorted_lines:
        posted = t.created_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        posted_s = posted.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        folio_rows.append(
            {
                "posted_at": posted_s,
                "tx_type": t.transaction_type,
                "category": t.category,
                "description": t.description or "—",
                "amount": _money(t.amount, currency),
            },
        )

    charges, payments, balance = _folio_totals(folio_lines)

    tax_block: dict[str, Any] | None = None
    if tax_config is not None and tax_config.tax_mode != TaxMode.off:
        breakdown = calculate_property_tax(charges, tax_config)
        rate_pct = (Decimal(str(tax_config.tax_rate)) * Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        tax_block = {
            "mode": tax_config.tax_mode.value,
            "name": tax_config.tax_name,
            "rate_pct": format(rate_pct, "f"),
            "summary_lines": property_tax_summary_lines(tax_config, breakdown),
            "net": format(breakdown.net_total, "f"),
            "tax_amount": format(breakdown.tax_amount, "f"),
            "gross": format(breakdown.gross_total, "f"),
        }

    ext_id = booking.external_booking_id or ""

    return {
        "property_name": property_.name,
        "currency_code": currency,
        "generated_at": gen_str,
        "guest_display": guest_display,
        "booking_id": str(booking.id),
        "external_booking_id": ext_id,
        "booking_status": booking.status,
        "check_in": check_in,
        "check_out": check_out,
        "room_display": room_name or "",
        "folio_rows": folio_rows,
        "tax": tax_block,
        "charges_total": _money(charges, currency),
        "payments_total": _money(payments, currency),
        "balance_due": _money(balance, currency),
    }


def _html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


async def generate_invoice_pdf(
    booking: Booking,
    folio_lines: list[FolioTransaction],
    tax_config: TaxConfig | None,
    property_: Property,
    *,
    guest: Guest | None = None,
    booking_lines: Sequence[BookingLine] | None = None,
    room_name: str | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    """Render ``invoice.html`` and return PDF bytes (CPU work offloaded to a thread)."""
    context = build_invoice_pdf_context(
        booking,
        property_,
        folio_lines,
        tax_config,
        guest=guest,
        booking_lines=booking_lines,
        room_name=room_name,
        generated_at=generated_at,
    )
    html = render_email("invoice.html", context)
    return await asyncio.to_thread(_html_to_pdf, html)
