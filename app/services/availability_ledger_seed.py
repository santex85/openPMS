"""Seed availability_ledger with daily rows (empty inventory) for a room type."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rates.availability_ledger import AvailabilityLedger


async def seed_empty_availability_ledger_year_forward(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    room_type_id: UUID,
    total_rooms: int,
    start_date: date | None = None,
    horizon_days: int = 365,
) -> None:
    """
    Insert one ledger row per day for ``horizon_days`` starting at ``start_date``
    (default: current UTC date). Booked/blocked are zero; ``total_rooms`` is
    typically the live count of physical rooms for that type (often 0 at creation).
    """
    start = start_date or datetime.now(timezone.utc).date()
    rows: list[AvailabilityLedger] = []
    for offset in range(horizon_days):
        day = start + timedelta(days=offset)
        rows.append(
            AvailabilityLedger(
                tenant_id=tenant_id,
                room_type_id=room_type_id,
                date=day,
                total_rooms=total_rooms,
                booked_rooms=0,
                blocked_rooms=0,
            )
        )
    session.add_all(rows)
    await session.flush()
