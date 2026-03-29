"""Housekeeping board and room status updates with audit log."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking_line import BookingLine
from app.models.core.room import Room
from app.models.core.room_housekeeping_event import RoomHousekeepingEvent
from app.models.core.room_type import RoomType
from app.schemas.housekeeping import HousekeepingPatchRequest, HousekeepingRoomRead


VALID_HK_STATUSES = frozenset({"clean", "dirty", "inspected", "out_of_service"})
VALID_HK_PRIORITIES = frozenset({"low", "normal", "high", "rush"})

class HousekeepingServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def list_rooms_for_housekeeping(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID,
    housekeeping_status: str | None = None,
    housekeeping_priority: str | None = None,
    filter_date: date | None = None,
) -> list[HousekeepingRoomRead]:
    stmt = (
        select(Room, RoomType.name)
        .join(
            RoomType,
            (RoomType.tenant_id == Room.tenant_id)
            & (RoomType.id == Room.room_type_id),
        )
        .where(
            Room.tenant_id == tenant_id,
            Room.deleted_at.is_(None),
            RoomType.property_id == property_id,
        )
    )
    if housekeeping_status is not None:
        hs = housekeeping_status.strip().lower()
        if hs not in VALID_HK_STATUSES:
            raise HousekeepingServiceError(
                f"status filter must be one of: {', '.join(sorted(VALID_HK_STATUSES))}",
                status_code=422,
            )
        stmt = stmt.where(Room.housekeeping_status == hs)
    if housekeeping_priority is not None:
        hp = housekeeping_priority.strip().lower()
        if hp not in VALID_HK_PRIORITIES:
            raise HousekeepingServiceError(
                f"priority filter must be one of: {', '.join(sorted(VALID_HK_PRIORITIES))}",
                status_code=422,
            )
        stmt = stmt.where(Room.housekeeping_priority == hp)
    if filter_date is not None:
        bl_exists = (
            exists()
            .where(
                BookingLine.tenant_id == Room.tenant_id,
                BookingLine.room_id == Room.id,
                BookingLine.date == filter_date,
            )
        )
        stmt = stmt.where(bl_exists)

    stmt = stmt.order_by(Room.name.asc())
    result = await session.execute(stmt)
    out: list[HousekeepingRoomRead] = []
    for room, rt_name in result.all():
        out.append(
            HousekeepingRoomRead(
                id=room.id,
                tenant_id=room.tenant_id,
                property_id=property_id,
                room_type_id=room.room_type_id,
                room_type_name=rt_name,
                name=room.name,
                status=room.status,
                housekeeping_status=room.housekeeping_status,
                housekeeping_priority=room.housekeeping_priority,
            ),
        )
    return out


async def patch_room_housekeeping(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
    body: HousekeepingPatchRequest,
    *,
    actor_user_id: UUID | None,
) -> Room:
    room = await session.scalar(
        select(Room).where(
            Room.tenant_id == tenant_id,
            Room.id == room_id,
            Room.deleted_at.is_(None),
        ),
    )
    if room is None:
        raise HousekeepingServiceError("room not found", status_code=404)

    new_status = body.housekeeping_status.strip().lower()
    if new_status not in VALID_HK_STATUSES:
        raise HousekeepingServiceError(
            f"housekeeping_status must be one of: {', '.join(sorted(VALID_HK_STATUSES))}",
            status_code=422,
        )

    old_status = room.housekeeping_status
    new_priority = room.housekeeping_priority
    if body.housekeeping_priority is not None:
        p = body.housekeeping_priority.strip().lower()
        if p not in VALID_HK_PRIORITIES:
            raise HousekeepingServiceError(
                f"housekeeping_priority must be one of: {', '.join(sorted(VALID_HK_PRIORITIES))}",
                status_code=422,
            )
        new_priority = p

    room.housekeeping_status = new_status
    room.housekeeping_priority = new_priority

    # Inspected: mark room ready for check-in when not under maintenance or OOO.
    if new_status == "inspected" and room.status not in ("maintenance", "out_of_order"):
        room.status = "available"

    session.add(
        RoomHousekeepingEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            room_id=room_id,
            old_status=old_status,
            new_status=new_status,
            changed_by_user_id=actor_user_id,
        ),
    )
    await session.flush()
    return room
