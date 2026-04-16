"""PDF invoice: context builder and WeasyPrint output (no DB)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.billing.tax_config import TaxConfig, TaxMode
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.services.pdf_invoice_service import (
    build_invoice_pdf_context,
    generate_invoice_pdf,
)


def _now() -> datetime:
    return datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)


def _base_entities() -> tuple:
    tid = uuid4()
    pid = uuid4()
    bid = uuid4()
    gid = uuid4()
    rt_id = uuid4()
    now = _now()

    prop = Property(
        id=pid,
        tenant_id=tid,
        name="Seaside Inn",
        country_pack_code=None,
        timezone="Asia/Bangkok",
        currency="THB",
        checkin_time=time(14, 0),
        checkout_time=time(12, 0),
    )
    booking = Booking(
        id=bid,
        tenant_id=tid,
        property_id=pid,
        guest_id=gid,
        rate_plan_id=None,
        external_booking_id="OTA-42",
        status="confirmed",
        source="direct",
        total_amount=Decimal("200.00"),
    )
    guest = Guest(
        id=gid,
        tenant_id=tid,
        first_name="Ann",
        last_name="Lee",
        email="ann@example.com",
        phone="+66-1",
        passport_data=None,
        nationality=None,
        date_of_birth=None,
        notes=None,
        vip_status=False,
        created_at=now,
        updated_at=now,
    )
    line = BookingLine(
        id=uuid4(),
        tenant_id=tid,
        booking_id=bid,
        date=date(2026, 6, 10),
        room_type_id=rt_id,
        room_id=None,
        price_for_date=Decimal("100.00"),
    )
    charge = FolioTransaction(
        id=uuid4(),
        tenant_id=tid,
        booking_id=bid,
        transaction_type="Charge",
        amount=Decimal("100.00"),
        payment_method=None,
        description="Room night",
        created_at=now,
        created_by=None,
        category="room_charge",
        source_channel=None,
    )
    return prop, booking, guest, line, charge


def test_build_invoice_pdf_context_inclusive_tax() -> None:
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.inclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.07"),
        created_at=now,
        updated_at=now,
    )
    ctx = build_invoice_pdf_context(
        booking,
        prop,
        [charge],
        tax,
        guest=guest,
        booking_lines=[line],
        room_name="101",
        generated_at=now,
    )
    assert ctx["tax"] is not None
    assert ctx["tax"]["mode"] == "inclusive"
    assert ctx["check_in"] == "2026-06-10"
    assert ctx["check_out"] == "2026-06-11"
    assert ctx["room_display"] == "101"
    assert len(ctx["folio_rows"]) == 1
    assert "Includes VAT" in ctx["tax"]["summary_lines"][0]


def test_build_invoice_pdf_context_exclusive_tax() -> None:
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.exclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.10"),
        created_at=now,
        updated_at=now,
    )
    ctx = build_invoice_pdf_context(
        booking,
        prop,
        [charge],
        tax,
        guest=guest,
        booking_lines=[line],
    )
    assert ctx["tax"] is not None
    assert ctx["tax"]["mode"] == "exclusive"
    assert ctx["tax"]["gross"] == "110.00"


def test_build_invoice_pdf_context_tax_off() -> None:
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.off,
        tax_name="None",
        tax_rate=Decimal("0"),
        created_at=now,
        updated_at=now,
    )
    ctx = build_invoice_pdf_context(
        booking,
        prop,
        [charge],
        tax,
        guest=guest,
    )
    assert ctx["tax"] is None


def test_build_invoice_pdf_context_empty_folio() -> None:
    prop, booking, guest, _, _ = _base_entities()
    ctx = build_invoice_pdf_context(
        booking,
        prop,
        [],
        None,
        guest=guest,
    )
    assert ctx["folio_rows"] == []
    assert ctx["charges_total"] == "0.00 THB"
    assert ctx["balance_due"] == "0.00 THB"


def _weasyprint_or_skip() -> None:
    try:
        import weasyprint  # noqa: F401
    except OSError:
        pytest.skip(
            "WeasyPrint native libraries unavailable on this host "
            "(install GTK/Pango stack or run tests in Docker runtime image).",
        )


@pytest.mark.asyncio
async def test_generate_invoice_pdf_magic_bytes() -> None:
    _weasyprint_or_skip()

    prop, booking, guest, line, charge = _base_entities()
    pdf = await generate_invoice_pdf(
        booking,
        [charge],
        None,
        prop,
        guest=guest,
        booking_lines=[line],
        generated_at=_now(),
    )
    assert pdf[:4] == b"%PDF"
    assert b"Invoice" in pdf
    assert b"Seaside Inn" in pdf


@pytest.mark.asyncio
async def test_generate_invoice_pdf_includes_tax_phrase_in_bytes() -> None:
    _weasyprint_or_skip()
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.inclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.07"),
        created_at=now,
        updated_at=now,
    )
    pdf = await generate_invoice_pdf(
        booking,
        [charge],
        tax,
        prop,
        guest=guest,
        booking_lines=[line],
        generated_at=now,
    )
    assert pdf[:4] == b"%PDF"
    assert b"Includes VAT" in pdf


@pytest.mark.asyncio
async def test_generate_invoice_pdf_exclusive_tax_shows_gross_in_bytes() -> None:
    _weasyprint_or_skip()
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.exclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.10"),
        created_at=now,
        updated_at=now,
    )
    pdf = await generate_invoice_pdf(
        booking,
        [charge],
        tax,
        prop,
        guest=guest,
        booking_lines=[line],
        generated_at=now,
    )
    assert pdf[:4] == b"%PDF"
    assert b"Gross" in pdf
    assert b"exclusive" in pdf


@pytest.mark.asyncio
async def test_generate_invoice_pdf_tax_off_no_tax_box_in_bytes() -> None:
    _weasyprint_or_skip()
    prop, booking, guest, line, charge = _base_entities()
    now = _now()
    tax = TaxConfig(
        id=uuid4(),
        tenant_id=booking.tenant_id,
        property_id=booking.property_id,
        tax_mode=TaxMode.off,
        tax_name="None",
        tax_rate=Decimal("0"),
        created_at=now,
        updated_at=now,
    )
    pdf = await generate_invoice_pdf(
        booking,
        [charge],
        tax,
        prop,
        guest=guest,
        booking_lines=[line],
        generated_at=now,
    )
    assert pdf[:4] == b"%PDF"
    assert b"Tax (" not in pdf
