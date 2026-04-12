"""Pytest fixtures: JWT, HTTP client, DB seeding (Postgres + RLS)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta, time
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.room import Room
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan

os.environ.setdefault("JWT_SECRET", "pytest-jwt-secret-key-minimum-32-characters!!")
os.environ.setdefault("ALLOW_PUBLIC_REGISTRATION", "true")
os.environ.setdefault("REFRESH_COOKIE_SECURE", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://openpms:openpms@127.0.0.1:5432/openpms_test",
)
os.environ.setdefault(
    "STRIPE_SECRET_KEY",
    "sk_test_openpms_pytest_placeholder_key_min_len_______________",
)
os.environ.setdefault(
    "STRIPE_CLIENT_ID",
    "ca_openpms_pytest_client_id_placeholder________________",
)
os.environ.setdefault(
    "STRIPE_REDIRECT_URI",
    "http://test/stripe/oauth/callback",
)
os.environ.setdefault(
    "STRIPE_CONNECT_SUCCESS_URL",
    "http://test/stripe/success",
)

pytest_plugins = (
    "tests.test_channex_webhook_sync",
    "tests.test_booking_room_conflict",
)

from app.main import app
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant

from app.core.config import clear_settings_cache

from tests.db_seed import disable_row_security_for_test_seed


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


@pytest.fixture
def auth_headers(jwt_secret: str):
    """
    Build ``Authorization: Bearer`` for integration tests.

    Always includes ``role`` (required by ``require_roles`` for JWT auth).
    Pass ``user_id`` when the route needs a real subject (``UserIdDep``) or
    audit attribution; otherwise ``sub`` is a random UUID (fine for RLS-only reads).
    """

    def _make(
        tenant_id: UUID,
        *,
        user_id: UUID | None = None,
        role: str = "owner",
    ) -> dict[str, str]:
        now = datetime.now(UTC)
        sub = str(user_id) if user_id is not None else str(uuid4())
        token = jwt.encode(
            {
                "tenant_id": str(tenant_id),
                "sub": sub,
                "role": role.strip().lower(),
                "exp": now + timedelta(hours=1),
            },
            jwt_secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def auth_headers_user(jwt_secret: str):
    """JWT with sub and role for routes that require UserIdDep (e.g. folio POST/DELETE)."""

    def _make(
        tenant_id: UUID,
        user_id: UUID,
        *,
        role: str = "receptionist",
    ) -> dict[str, str]:
        token = jwt.encode(
            {
                "tenant_id": str(tenant_id),
                "sub": str(user_id),
                "role": role,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
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
def smoke_scenario(db_engine: object) -> dict[str, UUID]:
    """
    Single-tenant smoke tenant with owner user, property, room type, one dirty room.
    """

    async def _seed() -> dict[str, UUID]:
        tenant_id = uuid4()
        owner_id = uuid4()
        manager_id = uuid4()
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="SmokeTenant",
                        billing_email="smoke@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="owner@smoke.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                session.add(
                    User(
                        id=manager_id,
                        tenant_id=tenant_id,
                        email="manager@smoke.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Manager",
                        role="manager",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Smoke Property",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                room_type = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="Standard",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(room_type)
                await session.flush()
                rate_plan = RatePlan(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rate_plan)
                await session.flush()
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        rate_plan_id=rate_plan.id,
                        date=date(2026, 6, 1),
                        price=Decimal("99.00"),
                    ),
                )
                room = Room(
                    tenant_id=tenant_id,
                    room_type_id=room_type.id,
                    name="101",
                    status="available",
                    housekeeping_status="dirty",
                    housekeeping_priority="normal",
                )
                session.add(room)
                await session.flush()
                property_id = prop.id
                room_id = room.id
                room_type_id = room_type.id
                rate_plan_id = rate_plan.id

        return {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "manager_id": manager_id,
            "property_id": property_id,
            "room_id": room_id,
            "room_type_id": room_type_id,
            "rate_plan_id": rate_plan_id,
        }

    return asyncio.run(_seed())


@pytest.fixture
def tenant_isolation_booking_scenario(db_engine):
    """
    Two tenants; tenant A has one booking. Used for RLS isolation assertions.
    """

    async def _seed() -> dict:
        tenant_a = uuid4()
        tenant_b = uuid4()
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
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
                room_type = RoomType(
                    tenant_id=tenant_a,
                    property_id=prop.id,
                    name="Standard",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(room_type)
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
                for i in range(3):
                    session.add(
                        BookingLine(
                            tenant_id=tenant_a,
                            booking_id=booking.id,
                            date=date(2026, 3, 1) + timedelta(days=i),
                            room_type_id=room_type.id,
                            room_id=None,
                            price_for_date=Decimal("33.34"),
                        ),
                    )
                await session.flush()
                booking_id = booking.id
                property_id = prop.id
                guest_id = guest.id

        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "booking_id": booking_id,
            "property_id": property_id,
            "guest_id": guest_id,
        }

    return asyncio.run(_seed())


async def _seed_folio_scenario(*, booking_status: str = "checked_in") -> dict[str, object]:
    tenant_id = uuid4()
    user_id = uuid4()
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is required to seed folio scenario")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    stay_nights = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="FolioTenant",
                    billing_email="folio@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Folio Property",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            room_type = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Standard",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(room_type)
            await session.flush()
            rate_plan = RatePlan(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="BAR",
                cancellation_policy="none",
            )
            session.add(rate_plan)
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email="reception@folio.example.com",
                    password_hash=hash_password("secret"),
                    full_name="Front Desk",
                    role="receptionist",
                ),
            )
            guest = Guest(
                tenant_id=tenant_id,
                first_name="F",
                last_name="Guest",
                email="fg@folio.example.com",
                phone="+10000000001",
            )
            session.add(guest)
            await session.flush()
            for night in stay_nights:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        rate_plan_id=rate_plan.id,
                        date=night,
                        price=Decimal("50.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=room_type.id,
                        date=night,
                        total_rooms=10,
                        booked_rooms=1,
                        blocked_rooms=0,
                    ),
                )
            booking = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                rate_plan_id=rate_plan.id,
                status=booking_status,
                source="test",
                total_amount=Decimal("150.00"),
            )
            session.add(booking)
            await session.flush()
            for night in stay_nights:
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking.id,
                        date=night,
                        room_type_id=room_type.id,
                        room_id=None,
                        price_for_date=Decimal("50.00"),
                    ),
                )
            session.add(
                FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    transaction_type="Charge",
                    amount=Decimal("150.00"),
                    payment_method=None,
                    description="Room charge (stay)",
                    created_by=None,
                    category="room_charge",
                ),
            )
            await session.flush()
            booking_id = booking.id
            seeded_property_id = prop.id

    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "booking_id": booking_id,
        "property_id": seeded_property_id,
    }


@pytest.fixture
def folio_scenario() -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(_seed_folio_scenario(booking_status="checked_in"))


@pytest.fixture
def folio_scenario_confirmed() -> dict[str, object]:
    """Booking left *confirmed* for FSM tests that forbid skip to checked_out."""
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(_seed_folio_scenario(booking_status="confirmed"))
