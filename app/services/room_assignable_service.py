"""Physical rooms assignable for a stay (no overlapping active booking on room)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.schemas.rooms import AssignableRoomsQueryParams
from app.services.room_list_service import property_belongs_to_tenant
from app.services.stay_dates import iter_stay_nights


async def list_assignable_rooms_for_stay(
    session: AsyncSession,
    tenant_id: UUID,
    params: AssignableRoomsQueryParams,
) -> list[Room] | None:
    """
    Return rooms of ``room_type_id`` on ``property_id`` with ``status == available``,
    not booked on any stay night by another active booking.

    Returns None if the property is unknown for the tenant or room type does not
    belong to that property.
    """
    if not await property_belongs_to_tenant(session, tenant_id, params.property_id):
        return None

    rt = await session.scalar(
        select(RoomType).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == params.room_type_id,
            RoomType.property_id == params.property_id,
            RoomType.deleted_at.is_(None),
        ),
    )
    if rt is None:
        return None

    nights = iter_stay_nights(params.check_in, params.check_out)

    rooms_stmt = (
        select(Room)
        .where(
            Room.tenant_id == tenant_id,
            Room.room_type_id == params.room_type_id,
            Room.deleted_at.is_(None),
            Room.status == "available",
        )
        .order_by(Room.name.asc())
    )
    rooms_result = await session.execute(rooms_stmt)
    rooms = list(rooms_result.scalars().all())
    if not rooms:
        return []

    room_ids = [r.id for r in rooms]
    busy_stmt = (
        select(BookingLine.room_id)
        .distinct()
        .join(
            Booking,
            (Booking.tenant_id == BookingLine.tenant_id)
            & (Booking.id == BookingLine.booking_id),
        )
        .where(
            BookingLine.tenant_id == tenant_id,
            BookingLine.room_id.in_(room_ids),
            BookingLine.date.in_(nights),
            Booking.status.notin_(["cancelled", "no_show"]),
        )
    )
    busy_result = await session.execute(busy_stmt)
    busy_ids = {row[0] for row in busy_result.all() if row[0] is not None}
    return [r for r in rooms if r.id not in busy_ids]
