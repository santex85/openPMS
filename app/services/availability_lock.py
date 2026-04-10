"""Pessimistic locking on availability_ledger rows for booking."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rates.availability_ledger import AvailabilityLedger


class LedgerNotSeededError(Exception):
    """Not all stay nights have a persisted availability_ledger row."""

    def __init__(
        self, message: str = "availability ledger not seeded for one or more dates"
    ) -> None:
        super().__init__(message)


class InsufficientInventoryError(Exception):
    """Not enough sellable rooms for at least one night."""

    def __init__(
        self, message: str = "insufficient inventory for one or more dates"
    ) -> None:
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


async def claim_availability_for_new_booking(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    nights: Sequence[date],
    *,
    rooms_to_book: int = 1,
) -> None:
    """
    Atomically increment booked_rooms for each stay night if inventory allows.

    Uses a conditional UPDATE so concurrent creators cannot both overbook the same
    bucket under high parallelism (ORM read/modify/flush alone is insufficient).
    """
    night_list = list(nights)
    if not night_list:
        return
    night_set = set(night_list)
    if len(night_set) != len(night_list):
        raise LedgerNotSeededError

    night_list_sorted = sorted(night_set)
    seeded = int(
        await session.scalar(
            select(func.count())
            .select_from(AvailabilityLedger)
            .where(
                AvailabilityLedger.tenant_id == tenant_id,
                AvailabilityLedger.room_type_id == room_type_id,
                AvailabilityLedger.date.in_(night_set),
            ),
        )
        or 0,
    )
    if seeded != len(night_set):
        raise LedgerNotSeededError

    # Single SQL statement: lock rows, conditional update (avoids multi-round-trip ORM races).
    stmt = text(
        """
WITH lk AS (
  SELECT id
  FROM availability_ledger
  WHERE tenant_id = CAST(:tid AS uuid)
    AND room_type_id = CAST(:rt AS uuid)
    AND date IN :dates
  ORDER BY date
  FOR UPDATE
),
u AS (
  UPDATE availability_ledger AS al
  SET booked_rooms = al.booked_rooms + :delta
  FROM lk
  WHERE al.id = lk.id
    AND al.booked_rooms + al.blocked_rooms + :delta <= al.total_rooms
  RETURNING al.id
)
SELECT count(*)::int FROM u
""",
    ).bindparams(bindparam("dates", expanding=True))

    n_up = int(
        (
            await session.execute(
                stmt,
                {
                    "tid": str(tenant_id),
                    "rt": str(room_type_id),
                    "dates": night_list_sorted,
                    "delta": rooms_to_book,
                },
            )
        ).scalar_one(),
    )
    if n_up != len(night_list_sorted):
        raise InsufficientInventoryError


def increment_booked_rooms(rows: Sequence[AvailabilityLedger], delta: int = 1) -> None:
    for row in rows:
        row.booked_rooms += delta


def decrement_booked_rooms(rows: Sequence[AvailabilityLedger], delta: int = 1) -> None:
    for row in rows:
        row.booked_rooms -= delta
        if row.booked_rooms < 0:
            raise InsufficientInventoryError(
                "booked_rooms would go negative for a ledger date",
            )
