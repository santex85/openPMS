"""List physical rooms for a property (board rows)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType


async def list_rooms_for_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> list[Room]:
    stmt = (
        select(Room)
        .join(
            RoomType,
            (RoomType.tenant_id == Room.tenant_id)
            & (RoomType.id == Room.room_type_id),
        )
        .where(
            Room.tenant_id == tenant_id,
            RoomType.property_id == property_id,
            Room.deleted_at.is_(None),
        )
        .order_by(Room.name.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def property_belongs_to_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> bool:
    stmt = select(Property.id).where(
        Property.tenant_id == tenant_id,
        Property.id == property_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row is not None
