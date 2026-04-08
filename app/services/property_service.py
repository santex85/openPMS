"""Property CRUD within tenant scope (RLS enforced via session)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.core.property import Property
from app.schemas.property import PropertyCreate, PropertyPatch


async def create_property(
    session: AsyncSession,
    tenant_id: UUID,
    data: PropertyCreate,
) -> Property:
    pack_code = (
        data.country_pack_code.strip() if data.country_pack_code else None
    )
    prop = Property(
        tenant_id=tenant_id,
        name=data.name.strip(),
        country_pack_code=pack_code,
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
        select(Property).where(Property.tenant_id == tenant_id).order_by(Property.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_bookings_for_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> int:
    n = await session.scalar(
        select(func.count(Booking.id)).where(
            Booking.property_id == property_id,
            Booking.tenant_id == tenant_id,
        ),
    )
    return int(n or 0)


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


async def update_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    data: PropertyPatch,
) -> Property | None:
    prop = await get_property(session, tenant_id, property_id)
    if prop is None:
        return None
    patch = data.model_dump(exclude_unset=True)
    if "name" in patch:
        prop.name = patch["name"].strip()
    if "timezone" in patch:
        prop.timezone = patch["timezone"]
    if "currency" in patch:
        prop.currency = patch["currency"]
    if "checkin_time" in patch:
        prop.checkin_time = patch["checkin_time"]
    if "checkout_time" in patch:
        prop.checkout_time = patch["checkout_time"]
    if "country_pack_code" in patch:
        c = patch["country_pack_code"]
        prop.country_pack_code = c.strip() if isinstance(c, str) and c.strip() else None
    await session.flush()
    return prop
