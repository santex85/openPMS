"""Unit tests for pricing_service."""

from datetime import date, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.services.pricing_service import MissingRatesError, sum_rates_for_stay

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url


@pytest.mark.asyncio
async def test_sum_rates_for_stay_same_day_raises(db_engine: object) -> None:
    """iter_stay_nights rejects check_out <= check_in; pricing surfaces that."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        with pytest.raises(ValueError, match="check_out"):
            await sum_rates_for_stay(
                session,
                uuid4(),
                uuid4(),
                uuid4(),
                date(2026, 1, 2),
                date(2026, 1, 1),
            )


@pytest.mark.asyncio
async def test_sum_rates_for_stay_raises_when_gap(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    tid = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    check_in = date(2026, 11, 1)
    check_out = date(2026, 11, 4)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                Tenant(
                    id=tid,
                    name="PricingSvcTenant",
                    billing_email="ps@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tid,
                name="PS Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tid,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            rp = RatePlan(
                tenant_id=tid,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rp)
            await session.flush()
            session.add(
                Rate(
                    tenant_id=tid,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=check_in,
                    price=Decimal("40.00"),
                ),
            )
            session.add(
                Rate(
                    tenant_id=tid,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=check_in + timedelta(days=1),
                    price=Decimal("40.00"),
                ),
            )
            rt_id = rt.id
            rp_id = rp.id

    gap_night = check_in + timedelta(days=2)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(MissingRatesError) as ei:
                await sum_rates_for_stay(
                    session,
                    tid,
                    rt_id,
                    rp_id,
                    check_in,
                    check_out,
                )
            assert gap_night in ei.value.missing_dates
