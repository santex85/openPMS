"""CRUD for physical rooms and availability ledger totals."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.rates.availability_ledger import AvailabilityLedger
from app.schemas.rooms import RoomBulkCreateItem


VALID_ROOM_STATUSES = frozenset({"available", "maintenance", "out_of_order"})

ACTIVE_BOOKING_STATUSES = frozenset(
    {"pending", "confirmed", "checked_in", "in_house"},
)


class RoomServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def recalculate_ledger_total_rooms(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    *,
    from_date: date | None = None,
) -> None:
    start = from_date or datetime.now(UTC).date()
    stmt_count = (
        select(func.count())
        .select_from(Room)
        .where(
            Room.tenant_id == tenant_id,
            Room.room_type_id == room_type_id,
            Room.deleted_at.is_(None),
        )
    )
    total = int((await session.execute(stmt_count)).scalar_one())
    await session.execute(
        update(AvailabilityLedger)
        .where(
            AvailabilityLedger.tenant_id == tenant_id,
            AvailabilityLedger.room_type_id == room_type_id,
            AvailabilityLedger.date >= start,
        )
        .values(total_rooms=total),
    )


async def list_rooms(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID | None = None,
) -> list[Room]:
    stmt = select(Room).where(
        Room.tenant_id == tenant_id,
        Room.deleted_at.is_(None),
    )
    if property_id is not None:
        stmt = stmt.join(
            RoomType,
            (RoomType.tenant_id == Room.tenant_id) & (RoomType.id == Room.room_type_id),
        ).where(RoomType.property_id == property_id)
    stmt = stmt.order_by(Room.name.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_room(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
) -> Room | None:
    return await session.scalar(
        select(Room).where(
            Room.tenant_id == tenant_id,
            Room.id == room_id,
            Room.deleted_at.is_(None),
        ),
    )


async def get_room_for_patch(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
) -> Room | None:
    """Include soft-deleted rows for idempotent PATCH/DELETE handling."""
    return await session.scalar(
        select(Room).where(
            Room.tenant_id == tenant_id,
            Room.id == room_id,
        ),
    )


async def create_room(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    room_type_id: UUID,
    name: str,
    status: str,
) -> Room:
    st = status.strip().lower()
    if st not in VALID_ROOM_STATUSES:
        raise RoomServiceError(
            f"status must be one of: {', '.join(sorted(VALID_ROOM_STATUSES))}",
            status_code=422,
        )
    rt = await session.scalar(
        select(RoomType).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == room_type_id,
            RoomType.deleted_at.is_(None),
        ),
    )
    if rt is None:
        raise RoomServiceError("room type not found", status_code=404)

    room = Room(
        tenant_id=tenant_id,
        room_type_id=room_type_id,
        name=name.strip(),
        status=st,
        deleted_at=None,
    )
    session.add(room)
    await session.flush()
    await recalculate_ledger_total_rooms(session, tenant_id, room_type_id)
    return room


async def create_rooms_bulk(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    room_type_id: UUID,
    items: list[RoomBulkCreateItem],
    on_conflict: Literal["skip", "fail"],
) -> tuple[list[Room], list[str]]:
    """Create many rooms for one category; recalculate availability ledger once."""
    rt = await session.scalar(
        select(RoomType).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == room_type_id,
            RoomType.deleted_at.is_(None),
        ),
    )
    if rt is None:
        raise RoomServiceError("room type not found", status_code=404)

    batch_seen: set[str] = set()
    for item in items:
        name = item.name.strip()
        if name in batch_seen:
            raise RoomServiceError(
                "duplicate room name in batch",
                status_code=422,
            )
        batch_seen.add(name)
        st = item.status.strip().lower()
        if st not in VALID_ROOM_STATUSES:
            raise RoomServiceError(
                f"status must be one of: {', '.join(sorted(VALID_ROOM_STATUSES))}",
                status_code=422,
            )

    stmt_names = select(Room.name).where(
        Room.tenant_id == tenant_id,
        Room.room_type_id == room_type_id,
        Room.deleted_at.is_(None),
    )
    res = await session.execute(stmt_names)
    existing_names = {row[0] for row in res.all()}

    created: list[Room] = []
    skipped: list[str] = []

    for item in items:
        name = item.name.strip()
        st = item.status.strip().lower()
        if name in existing_names:
            if on_conflict == "fail":
                raise RoomServiceError(
                    f"room name already exists: {name}",
                    status_code=409,
                )
            skipped.append(name)
            continue
        room = Room(
            tenant_id=tenant_id,
            room_type_id=room_type_id,
            name=name,
            status=st,
            deleted_at=None,
        )
        session.add(room)
        created.append(room)
        existing_names.add(name)

    if not created:
        await session.flush()
        return [], skipped

    await session.flush()
    await recalculate_ledger_total_rooms(session, tenant_id, room_type_id)
    return created, skipped


async def patch_room(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
    *,
    name: str | None = None,
    status: str | None = None,
    room_type_id: UUID | None = None,
) -> Room:
    room = await get_room_for_patch(session, tenant_id, room_id)
    if room is None or room.deleted_at is not None:
        raise RoomServiceError("room not found", status_code=404)

    old_type = room.room_type_id
    new_type = room_type_id if room_type_id is not None else old_type

    if room_type_id is not None and room_type_id != old_type:
        rt = await session.scalar(
            select(RoomType).where(
                RoomType.tenant_id == tenant_id,
                RoomType.id == room_type_id,
                RoomType.deleted_at.is_(None),
            ),
        )
        if rt is None:
            raise RoomServiceError("room type not found", status_code=404)
        room.room_type_id = room_type_id

    if name is not None:
        room.name = name.strip()

    if status is not None:
        st = status.strip().lower()
        if st not in VALID_ROOM_STATUSES:
            raise RoomServiceError(
                f"status must be one of: {', '.join(sorted(VALID_ROOM_STATUSES))}",
                status_code=422,
            )
        room.status = st

    await session.flush()

    if new_type != old_type:
        await recalculate_ledger_total_rooms(session, tenant_id, old_type)
        await recalculate_ledger_total_rooms(session, tenant_id, new_type)

    return room


async def soft_delete_room(
    session: AsyncSession,
    tenant_id: UUID,
    room_id: UUID,
) -> None:
    room = await get_room_for_patch(session, tenant_id, room_id)
    if room is None or room.deleted_at is not None:
        raise RoomServiceError("room not found", status_code=404)

    today = datetime.now(UTC).date()
    conflict = await session.scalar(
        select(BookingLine.id)
        .join(Booking, Booking.id == BookingLine.booking_id)
        .where(
            BookingLine.tenant_id == tenant_id,
            Booking.tenant_id == tenant_id,
            BookingLine.room_id == room_id,
            BookingLine.date >= today,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        )
        .limit(1),
    )
    if conflict is not None:
        raise RoomServiceError(
            "cannot delete room with future booking lines in active statuses",
            status_code=409,
        )

    room.deleted_at = datetime.now(UTC)
    await session.flush()
    await recalculate_ledger_total_rooms(session, tenant_id, room.room_type_id)
