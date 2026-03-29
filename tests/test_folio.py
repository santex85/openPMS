"""Folio balance, POST charge/payment, storno, checkout balance warning."""

from __future__ import annotations

import asyncio
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan


def _database_url() -> str | None:
    import os

    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_folio_scenario() -> dict[str, object]:
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
                status="confirmed",
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

    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "booking_id": booking_id,
    }


@pytest.fixture
def folio_scenario():
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(_seed_folio_scenario())


def test_get_folio_lists_transactions_and_balance(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    r = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(tenant_id),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["balance"] == "150.00"
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["transaction_type"] == "Charge"
    assert data["transactions"][0]["category"] == "room_charge"


def test_post_payment_reduces_balance(
    client,
    folio_scenario: dict,
    auth_headers,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    pr = client.post(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers_user(tenant_id, user_id),
        json={
            "entry_type": "payment",
            "amount": "50.00",
            "category": "payment",
            "payment_method": "cash",
        },
    )
    assert pr.status_code == 201
    gr = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(tenant_id),
    )
    assert gr.status_code == 200
    assert gr.json()["balance"] == "100.00"


def test_delete_folio_creates_reversal_row(
    client,
    folio_scenario: dict,
    auth_headers,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    mr = client.post(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers_user(tenant_id, user_id),
        json={
            "entry_type": "charge",
            "amount": "25.00",
            "category": "minibar",
            "description": "Snacks",
        },
    )
    assert mr.status_code == 201
    tx_id = mr.json()["id"]
    before = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(tenant_id),
    ).json()
    assert before["balance"] == "175.00"
    dr = client.delete(
        f"/bookings/{booking_id}/folio/{tx_id}",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert dr.status_code == 201
    assert "Reversal" in dr.json()["description"]
    after = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(tenant_id),
    ).json()
    assert after["balance"] == "150.00"
    assert len(after["transactions"]) == 3


def test_patch_checked_out_warns_when_folio_not_zero(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    r = client.patch(
        f"/bookings/{booking_id}",
        headers=auth_headers(tenant_id),
        json={"status": "checked_out"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["folio_balance_warning"] is True
    assert body["balance"] == "150.00"


def test_patch_checked_out_204_when_folio_settled(
    client,
    folio_scenario: dict,
    auth_headers,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    pay = client.post(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers_user(tenant_id, user_id),
        json={
            "entry_type": "payment",
            "amount": "150.00",
            "category": "payment",
            "payment_method": "card",
        },
    )
    assert pay.status_code == 201
    r = client.patch(
        f"/bookings/{booking_id}",
        headers=auth_headers(tenant_id),
        json={"status": "checked_out"},
    )
    assert r.status_code == 204
    assert r.text == ""
