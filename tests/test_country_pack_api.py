"""Country pack HTTP API (requires migrated DB + JWT)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, UTC, time
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from decimal import Decimal


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def pack_api_ctx(db_engine: object, jwt_secret: str) -> dict[str, object]:
    """Tenant with owner, property, guest, confirmed booking (no room assign)."""
    tenant_id = uuid4()
    owner_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="PackTenant",
                        billing_email="pack@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="owner@pack.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Pack Property",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                    country_pack_code="TH",
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
                guest = Guest(
                    tenant_id=tenant_id,
                    first_name="Pat",
                    last_name="Lee",
                    email="pat@example.com",
                    phone="+66",
                    passport_data=None,
                    nationality=None,
                )
                session.add(guest)
                await session.flush()
                booking = Booking(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=guest.id,
                    rate_plan_id=None,
                    status="confirmed",
                    source="test",
                    total_amount=Decimal("1000.00"),
                )
                session.add(booking)
                await session.flush()
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=booking.id,
                        date=datetime(2026, 7, 1).date(),
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("1000.00"),
                    ),
                )
                return {
                    "property_id": prop.id,
                    "booking_id": booking.id,
                    "guest_id": guest.id,
                }

    ids = asyncio.run(_seed())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "tenant_id": str(tenant_id),
            "sub": str(owner_id),
            "role": "owner",
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="http://test") as client:
        ids["client"] = client
        ids["headers"] = headers
        yield ids


@pytest.fixture
def pack_api_no_booking_ctx(db_engine: object, jwt_secret: str) -> dict[str, object]:
    """Tenant with owner, property (no country pack, no bookings)."""
    tenant_id = uuid4()
    owner_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="PackNoBookTenant",
                        billing_email="nobook@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="owner@nobook.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Empty Pack Property",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                    country_pack_code=None,
                )
                session.add(prop)
                await session.flush()
                return {"property_id": prop.id}

    ids = asyncio.run(_seed())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "tenant_id": str(tenant_id),
            "sub": str(owner_id),
            "role": "owner",
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="http://test") as client:
        ids["client"] = client
        ids["headers"] = headers
        yield ids


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_list_country_packs_includes_th(pack_api_ctx: dict[str, object]) -> None:
    client: TestClient = pack_api_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_ctx["headers"]  # type: ignore[assignment]
    r = client.get("/country-packs", headers=headers)
    assert r.status_code == 200
    codes = {row["code"] for row in r.json()}
    assert "TH" in codes
    assert "XX" in codes


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_apply_pack_ok_without_bookings(
    pack_api_no_booking_ctx: dict[str, object],
) -> None:
    client: TestClient = pack_api_no_booking_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_no_booking_ctx["headers"]  # type: ignore[assignment]
    pid = pack_api_no_booking_ctx["property_id"]
    r = client.post(
        "/country-packs/TH/apply",
        headers=headers,
        json={"property_id": str(pid)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["currency"] == "THB"
    assert body["timezone"] == "Asia/Bangkok"
    gr = client.get(f"/properties/{pid}", headers=headers)
    assert gr.status_code == 200
    assert gr.json()["country_pack_code"] == "TH"
    assert gr.json()["currency"] == "THB"


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_apply_pack_ok_change_pack_when_no_bookings(
    pack_api_no_booking_ctx: dict[str, object],
) -> None:
    """Switch TH -> XX on property with zero bookings (pack was already applied)."""
    client: TestClient = pack_api_no_booking_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_no_booking_ctx["headers"]  # type: ignore[assignment]
    pid = pack_api_no_booking_ctx["property_id"]
    r1 = client.post(
        "/country-packs/TH/apply",
        headers=headers,
        json={"property_id": str(pid)},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/country-packs/XX/apply",
        headers=headers,
        json={"property_id": str(pid)},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["country_pack_code"] == "XX"


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_apply_pack_409_with_bookings(pack_api_ctx: dict[str, object]) -> None:
    client: TestClient = pack_api_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_ctx["headers"]  # type: ignore[assignment]
    pid = pack_api_ctx["property_id"]
    r = client.post(
        "/country-packs/XX/apply",
        headers=headers,
        json={"property_id": str(pid)},
    )
    assert r.status_code == 409, r.text
    assert "booking" in r.json()["detail"].lower()


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_lock_status_unlocked(pack_api_no_booking_ctx: dict[str, object]) -> None:
    client: TestClient = pack_api_no_booking_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_no_booking_ctx["headers"]  # type: ignore[assignment]
    pid = pack_api_no_booking_ctx["property_id"]
    r = client.get(f"/properties/{pid}/lock-status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking_count"] == 0
    assert body["country_pack_locked"] is False
    assert body["property_id"] == str(pid)


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_lock_status_locked(pack_api_ctx: dict[str, object]) -> None:
    client: TestClient = pack_api_ctx["client"]  # type: ignore[assignment]
    headers: dict[str, str] = pack_api_ctx["headers"]  # type: ignore[assignment]
    pid = pack_api_ctx["property_id"]
    r = client.get(f"/properties/{pid}/lock-status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking_count"] >= 1
    assert body["country_pack_locked"] is True


@pytest.mark.skipif(not _database_url(), reason="DATABASE_URL not set")
def test_checkin_blocked_when_extension_requires_passport(
    db_engine: object,
    jwt_secret: str,
) -> None:
    """TM30-like extension with required_fields blocks check-in until guest data present."""
    from app.models.integrations.country_pack_extension import CountryPackExtension
    from app.models.integrations.property_extension import PropertyExtension

    tenant_id = uuid4()
    owner_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="ExtTenant",
                        billing_email="ext@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="own@example.com",
                        password_hash=hash_password("x"),
                        full_name="O",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="P",
                    timezone="UTC",
                    currency="THB",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                    country_pack_code="TH",
                )
                session.add(prop)
                await session.flush()
                ext = CountryPackExtension(
                    tenant_id=tenant_id,
                    country_code="TH",
                    code="th_tm30",
                    name="TM30",
                    webhook_url="https://example.com/hook",
                    required_fields=["passport_data", "nationality"],
                )
                session.add(ext)
                await session.flush()
                session.add(
                    PropertyExtension(
                        tenant_id=tenant_id,
                        property_id=prop.id,
                        extension_id=ext.id,
                        is_active=True,
                    ),
                )
                rt = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="Std",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(rt)
                await session.flush()
                guest = Guest(
                    tenant_id=tenant_id,
                    first_name="A",
                    last_name="B",
                    email="a@b.com",
                    phone="+1",
                )
                session.add(guest)
                await session.flush()
                book = Booking(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=guest.id,
                    status="confirmed",
                    source="t",
                    total_amount=Decimal("100.00"),
                )
                session.add(book)
                await session.flush()
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=book.id,
                        date=datetime(2026, 8, 1).date(),
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("100.00"),
                    ),
                )
                return {"bid": book.id}

    ids = asyncio.run(_seed())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "tenant_id": str(tenant_id),
            "sub": str(owner_id),
            "role": "owner",
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="http://test") as client:
        bad = client.patch(
            f"/bookings/{ids['bid']}",
            headers=headers,
            json={"status": "checked_in"},
        )
        assert bad.status_code == 400
        assert "passport" in bad.json()["detail"].lower() or "th_tm30" in bad.json()["detail"]
