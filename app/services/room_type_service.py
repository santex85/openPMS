"""Room type CRUD and availability ledger bootstrap."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.schemas.room_type import RoomTypeCreate, RoomTypePatch
from app.services.availability_ledger_seed import (
    seed_empty_availability_ledger_year_forward,
)
from app.services.property_service import get_property


async def count_rooms_for_room_type(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Room)
        .where(
            Room.tenant_id == tenant_id,
            Room.room_type_id == room_type_id,
            Room.deleted_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def create_room_type(
    session: AsyncSession,
    tenant_id: UUID,
    data: RoomTypeCreate,
) -> RoomType:
    prop = await get_property(session, tenant_id, data.property_id)
    if prop is None:
        raise ValueError("property not found")

    rt = RoomType(
        tenant_id=tenant_id,
        property_id=data.property_id,
        name=data.name.strip(),
        base_occupancy=data.base_occupancy,
        max_occupancy=data.max_occupancy,
        deleted_at=None,
    )
    session.add(rt)
    await session.flush()

    total_rooms = await count_rooms_for_room_type(session, tenant_id, rt.id)
    await seed_empty_availability_ledger_year_forward(
        session,
        tenant_id=tenant_id,
        room_type_id=rt.id,
        total_rooms=total_rooms,
    )
    return rt


async def list_room_types(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID | None,
) -> list[RoomType]:
    stmt = select(RoomType).where(
        RoomType.tenant_id == tenant_id,
        RoomType.deleted_at.is_(None),
    )
    if property_id is not None:
        stmt = stmt.where(RoomType.property_id == property_id)
    stmt = stmt.order_by(RoomType.name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_room_type(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
) -> RoomType | None:
    stmt = select(RoomType).where(
        RoomType.tenant_id == tenant_id,
        RoomType.id == room_type_id,
        RoomType.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_room_type_for_write(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
) -> RoomType | None:
    """Include soft-deleted rows for PATCH/DELETE idempotency."""
    stmt = select(RoomType).where(
        RoomType.tenant_id == tenant_id,
        RoomType.id == room_type_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def patch_room_type(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    body: RoomTypePatch,
) -> RoomType:
    rt = await get_room_type_for_write(session, tenant_id, room_type_id)
    if rt is None or rt.deleted_at is not None:
        raise ValueError("room type not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        rt.name = str(data["name"]).strip()
    if "base_occupancy" in data:
        rt.base_occupancy = int(data["base_occupancy"])
    if "max_occupancy" in data:
        rt.max_occupancy = int(data["max_occupancy"])
    if rt.max_occupancy < rt.base_occupancy:
        raise ValueError("max_occupancy must be >= base_occupancy")
    await session.flush()
    return rt


async def soft_delete_room_type(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
) -> None:
    rt = await get_room_type_for_write(session, tenant_id, room_type_id)
    if rt is None or rt.deleted_at is not None:
        raise ValueError("room type not found")
    rt.deleted_at = datetime.now(UTC)
    await session.flush()
