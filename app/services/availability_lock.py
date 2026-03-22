"""Pessimistic locking on availability_ledger rows for booking."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rates.availability_ledger import AvailabilityLedger


class LedgerNotSeededError(Exception):
    """Not all stay nights have a persisted availability_ledger row."""

    def __init__(self, message: str = "availability ledger not seeded for one or more dates") -> None:
        super().__init__(message)


class InsufficientInventoryError(Exception):
    """Not enough sellable rooms for at least one night."""

    def __init__(self, message: str = "insufficient inventory for one or more dates") -> None:
        super().__init__(message)


def _available_rooms(row: AvailabilityLedger) -> int:
    return row.total_rooms - row.booked_rooms - row.blocked_rooms


async def lock_and_validate_availability(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    nights: Sequence[date],
    *,
    rooms_to_book: int = 1,
) -> list[AvailabilityLedger]:
    """
    SELECT ... FOR UPDATE rows for (tenant, room_type, nights) ordered by date.

    Ensures one real ledger row per night and available >= rooms_to_book.
    """
    night_list = list(nights)
    if not night_list:
        return []

    stmt = (
        select(AvailabilityLedger)
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            AvailabilityLedger.room_type_id == room_type_id,
            AvailabilityLedger.date.in_(night_list),
        )
        .order_by(AvailabilityLedger.date.asc())
        .with_for_update()
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    if {r.date for r in rows} != set(night_list):
        raise LedgerNotSeededError

    for row in rows:
        if _available_rooms(row) < rooms_to_book:
            raise InsufficientInventoryError

    return sorted(rows, key=lambda r: r.date)


def increment_booked_rooms(rows: Sequence[AvailabilityLedger], delta: int = 1) -> None:
    for row in rows:
        row.booked_rooms += delta
