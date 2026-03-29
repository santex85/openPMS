"""Manual blocked_rooms overrides on availability_ledger."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.availability_override import AvailabilityOverridePutRequest
from app.services.availability_lock import LedgerNotSeededError


class AvailabilityOverrideError(Exception):
    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _iter_inclusive_dates(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


async def apply_blocked_rooms_override(
    session: AsyncSession,
    tenant_id: UUID,
    body: AvailabilityOverridePutRequest,
) -> int:
    dates = _iter_inclusive_dates(body.start_date, body.end_date)
    if not dates:
        return 0

    stmt = (
        select(AvailabilityLedger)
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            AvailabilityLedger.room_type_id == body.room_type_id,
            AvailabilityLedger.date.in_(dates),
        )
        .order_by(AvailabilityLedger.date.asc())
        .with_for_update()
    )
    result = await session.execute(stmt)
    ledger_rows = list(result.scalars().all())

    if len(ledger_rows) != len(dates):
        raise LedgerNotSeededError(
            "availability ledger must be seeded for every date in the override range",
        )

    for row in ledger_rows:
        max_block = row.total_rooms - row.booked_rooms
        if body.blocked_rooms > max_block:
            raise AvailabilityOverrideError(
                f"blocked_rooms exceeds capacity on {row.date.isoformat()} "
                f"(max {max_block} for current bookings)",
                status_code=409,
            )
        row.blocked_rooms = body.blocked_rooms

    return len(ledger_rows)
