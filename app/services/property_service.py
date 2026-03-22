"""Property CRUD within tenant scope (RLS enforced via session)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.property import Property
from app.schemas.property import PropertyCreate


async def create_property(
    session: AsyncSession,
    tenant_id: UUID,
    data: PropertyCreate,
) -> Property:
    prop = Property(
        tenant_id=tenant_id,
        name=data.name.strip(),
        timezone=data.timezone,
        currency=data.currency,
        checkin_time=data.checkin_time,
        checkout_time=data.checkout_time,
    )
    session.add(prop)
    await session.flush()
    return prop


async def list_properties(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[Property]:
    stmt = (
        select(Property)
        .where(Property.tenant_id == tenant_id)
        .order_by(Property.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> Property | None:
    stmt = select(Property).where(
        Property.tenant_id == tenant_id,
        Property.id == property_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
