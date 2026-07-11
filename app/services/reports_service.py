"""Property-scoped management reports: occupancy, revenue, ADR/RevPAR, CSV."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Iterator, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.reports import (
    KpiReport,
    OccupancyReport,
    OccupancyRow,
    RevenueReport,
    RevenueRow,
)

_ACTIVE_EXCLUDE = ("cancelled", "no_show")
_MONEY_Q = Decimal("0.01")
_ZERO = Decimal("0.00")


class ReportsServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _q2(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return _ZERO
    return Decimal(str(value)).quantize(_MONEY_Q)


def _money_str(value: Decimal) -> str:
    return format(_q2(value), "f")


def _pct_str(*, numerator: int | Decimal, denominator: int | Decimal) -> str:
    den = Decimal(str(denominator))
    if den == 0:
        return "0.00"
    num = Decimal(str(numerator))
    return format((num * Decimal("100") / den).quantize(_MONEY_Q), "f")


def _daterange(date_from: date, date_to: date) -> list[date]:
    days = (date_to - date_from).days + 1
    return [date_from + timedelta(days=i) for i in range(days)]


async def _require_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> Property:
    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise ReportsServiceError("property not found", status_code=404)
    return prop


def property_zoneinfo(prop: Property) -> ZoneInfo:
    tz_id = (prop.timezone or "").strip()
    if not tz_id:
        raise ReportsServiceError("property timezone is empty", status_code=400)
    try:
        return ZoneInfo(tz_id)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ReportsServiceError(
            f"Invalid IANA timezone for property: {prop.timezone!r}. "
            "Use a valid identifier such as Europe/Berlin or Asia/Bangkok.",
            status_code=400,
        ) from exc


async def _occupied_by_date(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, int]:
    stmt = (
        select(BookingLine.date, func.count())
        .select_from(BookingLine)
        .join(
            Booking,
            (Booking.tenant_id == BookingLine.tenant_id)
            & (Booking.id == BookingLine.booking_id),
        )
        .where(
            Booking.tenant_id == tenant_id,
            Booking.property_id == property_id,
            Booking.status.notin_(_ACTIVE_EXCLUDE),
            BookingLine.date >= date_from,
            BookingLine.date <= date_to,
        )
        .group_by(BookingLine.date)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def _available_by_date(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, int]:
    available_expr = func.coalesce(
        func.sum(AvailabilityLedger.total_rooms - AvailabilityLedger.blocked_rooms),
        0,
    )
    stmt = (
        select(AvailabilityLedger.date, available_expr)
        .select_from(AvailabilityLedger)
        .join(
            RoomType,
            (RoomType.tenant_id == AvailabilityLedger.tenant_id)
            & (RoomType.id == AvailabilityLedger.room_type_id),
        )
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            RoomType.property_id == property_id,
            AvailabilityLedger.date >= date_from,
            AvailabilityLedger.date <= date_to,
        )
        .group_by(AvailabilityLedger.date)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def _room_revenue_by_date(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> dict[date, Decimal]:
    stmt = (
        select(
            BookingLine.date,
            func.coalesce(func.sum(BookingLine.price_for_date), 0),
        )
        .select_from(BookingLine)
        .join(
            Booking,
            (Booking.tenant_id == BookingLine.tenant_id)
            & (Booking.id == BookingLine.booking_id),
        )
        .where(
            Booking.tenant_id == tenant_id,
            Booking.property_id == property_id,
            Booking.status.notin_(_ACTIVE_EXCLUDE),
            BookingLine.date >= date_from,
            BookingLine.date <= date_to,
        )
        .group_by(BookingLine.date)
    )
    result = await session.execute(stmt)
    return {row[0]: _q2(row[1]) for row in result.all()}


def _local_date_expr(timezone_id: str) -> Any:
    """Property-local calendar date of a timestamptz column."""
    return func.date(func.timezone(timezone_id, FolioTransaction.created_at))


async def _folio_aggregates_by_date(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    timezone_id: str,
    date_from: date,
    date_to: date,
) -> tuple[dict[date, dict[str, Decimal]], dict[date, Decimal], dict[date, Decimal]]:
    """Return (other_charges_by_date, tax_by_date, payments_by_date)."""
    local_date = _local_date_expr(timezone_id).label("local_date")
    stmt = (
        select(
            local_date,
            FolioTransaction.transaction_type,
            FolioTransaction.category,
            func.coalesce(func.sum(FolioTransaction.amount), 0),
        )
        .select_from(FolioTransaction)
        .join(
            Booking,
            (Booking.tenant_id == FolioTransaction.tenant_id)
            & (Booking.id == FolioTransaction.booking_id),
        )
        .where(
            FolioTransaction.tenant_id == tenant_id,
            Booking.property_id == property_id,
            local_date >= date_from,
            local_date <= date_to,
        )
        .group_by(
            local_date, FolioTransaction.transaction_type, FolioTransaction.category
        )
    )
    result = await session.execute(stmt)

    other: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: _ZERO)
    )
    tax: dict[date, Decimal] = defaultdict(lambda: _ZERO)
    payments: dict[date, Decimal] = defaultdict(lambda: _ZERO)

    for local_d, tx_type, category, amount in result.all():
        amt = _q2(amount)
        cat = str(category or "")
        if tx_type == "Payment":
            payments[local_d] = _q2(payments[local_d] + amt)
        elif tx_type == "Charge":
            if cat == "tax":
                tax[local_d] = _q2(tax[local_d] + amt)
            elif cat != "room_charge":
                other[local_d][cat] = _q2(other[local_d][cat] + amt)

    return (
        {d: dict(cats) for d, cats in other.items()},
        dict(tax),
        dict(payments),
    )


async def get_occupancy_report(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> OccupancyReport:
    prop = await _require_property(session, tenant_id, property_id)
    occupied = await _occupied_by_date(
        session, tenant_id, property_id, date_from, date_to
    )
    available = await _available_by_date(
        session, tenant_id, property_id, date_from, date_to
    )
    rows: list[OccupancyRow] = []
    for d in _daterange(date_from, date_to):
        occ = occupied.get(d, 0)
        avail = available.get(d, 0)
        rows.append(
            OccupancyRow(
                date=d,
                occupied_rooms=occ,
                available_rooms=avail,
                occupancy_pct=_pct_str(numerator=occ, denominator=avail),
            ),
        )
    return OccupancyReport(
        property_id=property_id,
        date_from=date_from,
        date_to=date_to,
        currency=prop.currency,
        rows=rows,
    )


async def get_revenue_report(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> RevenueReport:
    prop = await _require_property(session, tenant_id, property_id)
    property_zoneinfo(prop)  # validate IANA id
    tz_id = prop.timezone.strip()
    room_by_date = await _room_revenue_by_date(
        session, tenant_id, property_id, date_from, date_to
    )
    other_by_date, tax_by_date, pay_by_date = await _folio_aggregates_by_date(
        session,
        tenant_id,
        property_id,
        tz_id,
        date_from,
        date_to,
    )

    rows: list[RevenueRow] = []
    room_total = _ZERO
    other_totals: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    tax_total = _ZERO
    payments_total = _ZERO

    for d in _daterange(date_from, date_to):
        room = room_by_date.get(d, _ZERO)
        other = other_by_date.get(d, {})
        tax = tax_by_date.get(d, _ZERO)
        payments = pay_by_date.get(d, _ZERO)
        room_total = _q2(room_total + room)
        tax_total = _q2(tax_total + tax)
        payments_total = _q2(payments_total + payments)
        for cat, amt in other.items():
            other_totals[cat] = _q2(other_totals[cat] + amt)
        rows.append(
            RevenueRow(
                date=d,
                room_revenue=_money_str(room),
                other_charges={k: _money_str(v) for k, v in sorted(other.items())},
                tax_total=_money_str(tax),
                payments_total=_money_str(payments),
            ),
        )

    return RevenueReport(
        property_id=property_id,
        date_from=date_from,
        date_to=date_to,
        currency=prop.currency,
        rows=rows,
        room_revenue_total=_money_str(room_total),
        other_charges_total={k: _money_str(v) for k, v in sorted(other_totals.items())},
        tax_total=_money_str(tax_total),
        payments_total=_money_str(payments_total),
    )


async def get_kpi_report(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    date_from: date,
    date_to: date,
) -> KpiReport:
    prop = await _require_property(session, tenant_id, property_id)
    room_by_date = await _room_revenue_by_date(
        session, tenant_id, property_id, date_from, date_to
    )
    occupied = await _occupied_by_date(
        session, tenant_id, property_id, date_from, date_to
    )
    available = await _available_by_date(
        session, tenant_id, property_id, date_from, date_to
    )

    room_revenue = _ZERO
    sold_nights = 0
    available_nights = 0
    for d in _daterange(date_from, date_to):
        room_revenue = _q2(room_revenue + room_by_date.get(d, _ZERO))
        sold_nights += occupied.get(d, 0)
        available_nights += available.get(d, 0)

    if sold_nights == 0:
        adr = _ZERO
    else:
        adr = _q2(room_revenue / Decimal(sold_nights))
    if available_nights == 0:
        revpar = _ZERO
    else:
        revpar = _q2(room_revenue / Decimal(available_nights))

    return KpiReport(
        property_id=property_id,
        date_from=date_from,
        date_to=date_to,
        currency=prop.currency,
        sold_nights=sold_nights,
        available_nights=available_nights,
        room_revenue=_money_str(room_revenue),
        occupancy_pct=_pct_str(
            numerator=sold_nights,
            denominator=available_nights,
        ),
        adr=_money_str(adr),
        revpar=_money_str(revpar),
    )


def rows_to_csv(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> Iterator[str]:
    """Yield UTF-8 CSV chunks with a leading BOM for Excel compatibility."""
    yield "\ufeff"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(headers))
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    for row in rows:
        writer.writerow(list(row))
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


def occupancy_report_csv_rows(report: OccupancyReport) -> Iterator[str]:
    return rows_to_csv(
        ("date", "occupied_rooms", "available_rooms", "occupancy_pct"),
        (
            (r.date.isoformat(), r.occupied_rooms, r.available_rooms, r.occupancy_pct)
            for r in report.rows
        ),
    )


def revenue_report_csv_rows(report: RevenueReport) -> Iterator[str]:
    category_keys: list[str] = sorted(
        {cat for row in report.rows for cat in row.other_charges},
    )
    headers = (
        "date",
        "room_revenue",
        *category_keys,
        "tax_total",
        "payments_total",
    )

    def _iter_rows() -> Iterator[Sequence[Any]]:
        for r in report.rows:
            yield (
                r.date.isoformat(),
                r.room_revenue,
                *(r.other_charges.get(k, "0.00") for k in category_keys),
                r.tax_total,
                r.payments_total,
            )

    return rows_to_csv(headers, _iter_rows())


def kpi_report_csv_rows(report: KpiReport) -> Iterator[str]:
    return rows_to_csv(
        (
            "property_id",
            "date_from",
            "date_to",
            "currency",
            "sold_nights",
            "available_nights",
            "room_revenue",
            "occupancy_pct",
            "adr",
            "revpar",
        ),
        (
            (
                str(report.property_id),
                report.date_from.isoformat(),
                report.date_to.isoformat(),
                report.currency,
                report.sold_nights,
                report.available_nights,
                report.room_revenue,
                report.occupancy_pct,
                report.adr,
                report.revpar,
            ),
        ),
    )
