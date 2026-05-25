"""Async DB seeds reused by TZ-19 booking HTTP tests."""

from __future__ import annotations

import os
from datetime import date, timedelta, time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan

from tests.db_seed import disable_row_security_for_test_seed


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def seed_rate_span(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    room_type_id: UUID,
    rate_plan_id: UUID,
    start: date,
    num_days: int,
    price: Decimal,
    total_rooms: int,
) -> None:
    """Rate + AvailabilityLedger rows for consecutive nights [start, start + num_days)."""
    for i in range(num_days):
        d = start + timedelta(days=i)
        session.add(
            Rate(
                tenant_id=tenant_id,
                room_type_id=room_type_id,
                rate_plan_id=rate_plan_id,
                date=d,
                price=price,
            ),
        )
        session.add(
            AvailabilityLedger(
                tenant_id=tenant_id,
                room_type_id=room_type_id,
                date=d,
                total_rooms=total_rooms,
                booked_rooms=0,
                blocked_rooms=0,
            ),
        )


async def seed_booking_post_environment(
    *,
    nights: list[date] | None = None,
    total_rooms: int = 10,
    price: Decimal = Decimal("55.00"),
    existing_guest_email: str | None = None,
) -> dict[str, UUID]:
    """Tenant + receptionist user + one property + BAR + optional pre-seeded Guest + rates & ledger."""
    url = database_url()
    assert url
    tenant_id = uuid4()
    user_id = uuid4()
    if nights is None:
        nights = sorted(
            {
                date(2026, 8, 1),
                date(2026, 8, 2),
                date(2026, 9, 1),
                date(2026, 9, 2),
                date(2026, 11, 1),
                date(2026, 11, 2),
                date(2026, 12, 1),
            },
        )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="BookSeedTenant",
                    billing_email="bs@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Book Seed Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            rp = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rp)
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email="rcv@bookseed.example.com",
                    password_hash=hash_password("secret"),
                    full_name="Rec",
                    role="receptionist",
                ),
            )
            session.add(
                Room(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    name="101",
                    status="available",
                ),
            )
            if existing_guest_email:
                session.add(
                    Guest(
                        tenant_id=tenant_id,
                        first_name="Existing",
                        last_name="Guest",
                        email=existing_guest_email.lower().strip(),
                        phone="+10000000001",
                    ),
                )
            for night in nights:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=night,
                        price=price,
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=night,
                        total_rooms=total_rooms,
                        booked_rooms=0,
                        blocked_rooms=0,
                    ),
                )
            pid = prop.id
            rtid = rt.id
            rpid = rp.id
    await engine.dispose()
    out: dict[str, UUID | str | None] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "property_id": pid,
        "room_type_id": rtid,
        "rate_plan_id": rpid,
    }
    if existing_guest_email:
        out["guest_email"] = existing_guest_email
    return out  # type: ignore[return-value]
