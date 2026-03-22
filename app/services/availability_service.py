"""Availability grid for properties / room types."""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.inventory import AvailabilityCell, AvailabilityGridResponse
from app.services.property_service import get_property


async def _room_counts_by_type(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_ids: list[UUID],
) -> dict[UUID, int]:
    if not room_type_ids:
        return {}
    stmt = (
        select(Room.room_type_id, func.count())
        .where(
            Room.tenant_id == tenant_id,
            Room.room_type_id.in_(room_type_ids),
        )
        .group_by(Room.room_type_id)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def get_availability_grid(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID,
    start_date: date,
    end_date: date,
    room_type_id: UUID | None,
) -> AvailabilityGridResponse | None:
    prop = await get_property(session, tenant_id, property_id)
    if prop is None:
        return None

    rt_stmt = select(RoomType).where(
        RoomType.tenant_id == tenant_id,
        RoomType.property_id == property_id,
    )
    if room_type_id is not None:
        rt_stmt = rt_stmt.where(RoomType.id == room_type_id)
    rt_stmt = rt_stmt.order_by(RoomType.name)
    rt_result = await session.execute(rt_stmt)
    room_types = list(rt_result.scalars().all())
    if not room_types:
        return AvailabilityGridResponse(
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
            cells=[],
        )

    rt_ids = [rt.id for rt in room_types]
    counts = await _room_counts_by_type(session, tenant_id, rt_ids)

    dates: list[date] = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)

    leg_stmt = select(AvailabilityLedger).where(
        AvailabilityLedger.tenant_id == tenant_id,
        AvailabilityLedger.room_type_id.in_(rt_ids),
        AvailabilityLedger.date >= start_date,
        AvailabilityLedger.date <= end_date,
    )
    leg_result = await session.execute(leg_stmt)
    ledger_rows = list(leg_result.scalars().all())
    ledger_map: dict[tuple[UUID, date], AvailabilityLedger] = {
        (row.room_type_id, row.date): row for row in ledger_rows
    }

    cells: list[AvailabilityCell] = []
    for rt in room_types:
        default_total = counts.get(rt.id, 0)
        for day in dates:
            row = ledger_map.get((rt.id, day))
            if row is not None:
                total = row.total_rooms
                booked = row.booked_rooms
                blocked = row.blocked_rooms
            else:
                total = default_total
                booked = 0
                blocked = 0
            available = total - booked - blocked
            cells.append(
                AvailabilityCell(
                    date=day,
                    room_type_id=rt.id,
                    room_type_name=rt.name,
                    total_rooms=total,
                    booked_rooms=booked,
                    blocked_rooms=blocked,
                    available_rooms=available,
                )
            )

    return AvailabilityGridResponse(
        property_id=property_id,
        start_date=start_date,
        end_date=end_date,
        cells=cells,
    )
