"""Query and bulk upsert nightly rates."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.room_type import RoomType
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.schemas.nightly_rates import BulkRatesPutRequest


class RatesServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _iter_inclusive_dates(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


async def _require_room_type_and_plan_same_property(
    session: AsyncSession,
    tenant_id: UUID,
    room_type_id: UUID,
    rate_plan_id: UUID,
) -> None:
    rt = await session.scalar(
        select(RoomType).where(
            RoomType.tenant_id == tenant_id,
            RoomType.id == room_type_id,
        ),
    )
    rp = await session.scalar(
        select(RatePlan).where(
            RatePlan.tenant_id == tenant_id,
            RatePlan.id == rate_plan_id,
        ),
    )
    if rt is None:
        raise RatesServiceError("room_type not found", status_code=404)
    if rp is None:
        raise RatesServiceError("rate_plan not found", status_code=404)
    if rt.property_id != rp.property_id:
        raise RatesServiceError(
            "room type and rate plan must belong to the same property",
            status_code=409,
        )


async def list_rates_for_period(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    room_type_id: UUID,
    rate_plan_id: UUID,
    start_date: date,
    end_date: date,
) -> list[Rate]:
    if end_date < start_date:
        raise RatesServiceError(
            "end_date must be on or after start_date",
            status_code=422,
        )

    await _require_room_type_and_plan_same_property(
        session,
        tenant_id,
        room_type_id,
        rate_plan_id,
    )

    stmt = (
        select(Rate)
        .where(
            Rate.tenant_id == tenant_id,
            Rate.room_type_id == room_type_id,
            Rate.rate_plan_id == rate_plan_id,
            Rate.date >= start_date,
            Rate.date <= end_date,
        )
        .order_by(Rate.date.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def bulk_upsert_rates(
    session: AsyncSession,
    tenant_id: UUID,
    body: BulkRatesPutRequest,
) -> tuple[int, list[tuple[UUID, UUID, date, str]]]:
    by_key: dict[tuple[UUID, UUID, date], dict] = {}
    for seg in body.segments:
        await _require_room_type_and_plan_same_property(
            session,
            tenant_id,
            seg.room_type_id,
            seg.rate_plan_id,
        )
        for d in _iter_inclusive_dates(seg.start_date, seg.end_date):
            key = (seg.room_type_id, seg.rate_plan_id, d)
            by_key[key] = {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "room_type_id": seg.room_type_id,
                "rate_plan_id": seg.rate_plan_id,
                "date": d,
                "price": seg.price,
                "stop_sell": seg.stop_sell,
                "min_stay_arrival": seg.min_stay_arrival,
                "max_stay": seg.max_stay,
            }

    rows = list(by_key.values())
    if not rows:
        return 0, []

    stmt = insert(Rate).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_rates_tenant_room_type_plan_date",
        set_={
            "price": stmt.excluded.price,
            "stop_sell": stmt.excluded.stop_sell,
            "min_stay_arrival": stmt.excluded.min_stay_arrival,
            "max_stay": stmt.excluded.max_stay,
        },
    )
    await session.execute(stmt)
    updates = [
        (r["room_type_id"], r["rate_plan_id"], r["date"], format(r["price"], "f"))
        for r in rows
    ]
    return len(rows), updates
