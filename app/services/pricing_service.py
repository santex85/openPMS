"""Sum nightly rates from the rates table for a stay."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rates.rate import Rate
from app.services.stay_dates import iter_stay_nights


class MissingRatesError(Exception):
    """One or more nights have no rate row."""

    def __init__(self, missing_dates: list[date]) -> None:
        self.missing_dates = missing_dates
        super().__init__(f"missing rates for dates: {missing_dates!r}")


async def sum_rates_for_stay(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    rate_plan_id: UUID,
    check_in: date,
    check_out: date,
) -> tuple[Decimal, list[tuple[date, Decimal]]]:
    """
    Return (total, per_night list ordered by date) for nights in [check_in, check_out).
    """
    nights = iter_stay_nights(check_in, check_out)
    if not nights:
        return Decimal("0.00"), []

    stmt = select(Rate).where(
        Rate.tenant_id == tenant_id,
        Rate.room_type_id == room_type_id,
        Rate.rate_plan_id == rate_plan_id,
        Rate.date.in_(nights),
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    by_date: dict[date, Decimal] = {r.date: r.price for r in rows}

    missing = [d for d in nights if d not in by_date]
    if missing:
        raise MissingRatesError(missing)

    per_night: list[tuple[date, Decimal]] = [(d, by_date[d]) for d in nights]
    total: Decimal = sum((p for _, p in per_night), Decimal("0"))
    return total, per_night
