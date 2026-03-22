"""Pytest fixtures: JWT, HTTP client, DB seeding (Postgres + RLS)."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("JWT_SECRET", "pytest-jwt-secret-key-minimum-32-characters!!")

from app.main import app
from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


@pytest.fixture
def auth_headers(jwt_secret: str):
    def _make(tenant_id: UUID) -> dict[str, str]:
        token = jwt.encode(
            {"tenant_id": str(tenant_id)},
            jwt_secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def client():
    """Starlette TestClient runs app lifespan (async_session_factory on app.state)."""
    from starlette.testclient import TestClient

    with TestClient(app, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def db_engine():
    url = _database_url()
    if not url:
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")
    engine = create_async_engine(url)
    yield engine

    async def _dispose() -> None:
        await engine.dispose()

    asyncio.run(_dispose())


@pytest.fixture
def tenant_isolation_booking_scenario(db_engine):
    """
    Two tenants; tenant A has one booking. Used for RLS isolation assertions.
    """

    async def _seed() -> dict:
        tenant_a = uuid4()
        tenant_b = uuid4()
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                for tid, label in ((tenant_a, "TenantA"), (tenant_b, "TenantB")):
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                        ),
                        {"tid": str(tid)},
                    )
                    session.add(
                        Tenant(
                            id=tid,
                            name=label,
                            billing_email=f"{label.lower()}@example.com",
                            status="active",
                        ),
                    )
                    await session.flush()
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_a)},
                )
                prop = Property(
                    tenant_id=tenant_a,
                    name="Property A",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                guest = Guest(
                    tenant_id=tenant_a,
                    first_name="Ann",
                    last_name="A",
                    email="ann@a.example.com",
                    phone="+10000000001",
                )
                session.add(guest)
                await session.flush()
                booking = Booking(
                    tenant_id=tenant_a,
                    property_id=prop.id,
                    guest_id=guest.id,
                    status="confirmed",
                    source="test",
                    total_amount=Decimal("100.00"),
                )
                session.add(booking)
                await session.flush()
                booking_id = booking.id
                property_id = prop.id

        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "booking_id": booking_id,
            "property_id": property_id,
        }

    return asyncio.run(_seed())
