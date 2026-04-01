"""Seed availability_ledger with daily rows (empty inventory) for a room type."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.room_type import RoomType
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


async def extend_availability_ledger_days(
    session: AsyncSession,
    *,
    extra_days: int = 30,
) -> int:
    """
    For each ``room_types`` row visible to the session (under ``app.tenant_id`` RLS), append
    ``extra_days`` ledger day(s) beyond the current MAX(date), or seed ``extra_days`` days when
    no rows exist. Uses ON CONFLICT DO NOTHING so repeats are safe.

    Callers should set ``app.tenant_id`` when using a tenant-scoped DB role. With a superuser
    connection, iterate tenants in application code and set the variable per tenant.
    """
    if extra_days < 1:
        return 0

    # Imported lazily: room_type_service imports this module at load time.
    from app.services.room_type_service import count_rooms_for_room_type

    stmt = select(RoomType)
    result = await session.execute(stmt)
    room_types = list(result.scalars().all())

    inserted_total = 0
    for rt in room_types:
        tenant_id = rt.tenant_id
        room_type_id = rt.id
        max_date = await session.scalar(
            select(func.max(AvailabilityLedger.date)).where(
                AvailabilityLedger.tenant_id == tenant_id,
                AvailabilityLedger.room_type_id == room_type_id,
            )
        )
        total_rooms = await count_rooms_for_room_type(session, tenant_id, room_type_id)
        if max_date is None:
            await seed_empty_availability_ledger_year_forward(
                session,
                tenant_id=tenant_id,
                room_type_id=room_type_id,
                total_rooms=total_rooms,
                horizon_days=extra_days,
            )
            inserted_total += extra_days
            continue

        for i in range(1, extra_days + 1):
            day = max_date + timedelta(days=i)
            ins = (
                pg_insert(AvailabilityLedger)
                .values(
                    tenant_id=tenant_id,
                    room_type_id=room_type_id,
                    date=day,
                    total_rooms=total_rooms,
                    booked_rooms=0,
                    blocked_rooms=0,
                )
                .on_conflict_do_nothing(
                    constraint="uq_availability_ledger_tenant_room_type_date",
                )
                .returning(AvailabilityLedger.id)
            )
            res = await session.execute(ins)
            inserted_total += len(res.fetchall())

    return inserted_total
