"""CRUD for rate plans."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.schemas.rate_plan import RatePlanCreate, RatePlanPatch
from app.services.property_service import get_property


class RatePlanServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def list_rate_plans(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID | None = None,
) -> list[RatePlan]:
    stmt = select(RatePlan).where(RatePlan.tenant_id == tenant_id)
    if property_id is not None:
        stmt = stmt.where(RatePlan.property_id == property_id)
    stmt = stmt.order_by(RatePlan.name.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_rate_plan(
    session: AsyncSession,
    tenant_id: UUID,
    rate_plan_id: UUID,
) -> RatePlan | None:
    return await session.scalar(
        select(RatePlan).where(
            RatePlan.tenant_id == tenant_id,
            RatePlan.id == rate_plan_id,
        ),
    )


async def create_rate_plan(
    session: AsyncSession,
    tenant_id: UUID,
    body: RatePlanCreate,
) -> RatePlan:
    prop = await get_property(session, tenant_id, body.property_id)
    if prop is None:
        raise RatePlanServiceError("property not found", status_code=404)

    row = RatePlan(
        id=uuid4(),
        tenant_id=tenant_id,
        property_id=body.property_id,
        name=body.name.strip(),
        cancellation_policy=body.cancellation_policy.strip(),
    )
    session.add(row)
    await session.flush()
    return row


async def patch_rate_plan(
    session: AsyncSession,
    tenant_id: UUID,
    rate_plan_id: UUID,
    body: RatePlanPatch,
) -> RatePlan:
    row = await get_rate_plan(session, tenant_id, rate_plan_id)
    if row is None:
        raise RatePlanServiceError("rate plan not found", status_code=404)

    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        row.name = data["name"].strip()
    if "cancellation_policy" in data:
        row.cancellation_policy = data["cancellation_policy"].strip()
    await session.flush()
    return row


async def delete_rate_plan(
    session: AsyncSession,
    tenant_id: UUID,
    rate_plan_id: UUID,
) -> None:
    row = await get_rate_plan(session, tenant_id, rate_plan_id)
    if row is None:
        raise RatePlanServiceError("rate plan not found", status_code=404)

    ref = await session.scalar(
        select(Booking.id)
        .where(
            Booking.tenant_id == tenant_id,
            Booking.rate_plan_id == rate_plan_id,
        )
        .limit(1),
    )
    if ref is not None:
        raise RatePlanServiceError(
            "cannot delete rate plan referenced by bookings",
            status_code=409,
        )

    await session.execute(
        delete(Rate).where(
            Rate.tenant_id == tenant_id,
            Rate.rate_plan_id == rate_plan_id,
        ),
    )
    session.delete(row)
    await session.flush()
