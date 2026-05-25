"""HTTP PATCH booking status transitions (FSM nuances beyond unit tests)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def booking_pending_confirm_ctx(db_engine: object) -> dict[str, UUID]:
    """pending booking with BAR + ledger (confirmed transition path)."""

    async def _seed() -> dict[str, UUID]:
        tid = uuid4()
        uid = uuid4()
        nights = [date(2033, 5, 1), date(2033, 5, 2)]
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
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
                        name="PendTz19",
                        billing_email="p@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tid,
                    name="Tp",
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
                    User(
                        id=uid,
                        tenant_id=tid,
                        email=f"u{uid.hex[:8]}@example.com",
                        password_hash=hash_password("secret"),
                        full_name="R",
                        role="receptionist",
                    ),
                )
                g = Guest(
                    tenant_id=tid,
                    first_name="P",
                    last_name="D",
                    email="pendingg@fz.example.com",
                    phone="+1",
                )
                session.add(g)
                await session.flush()
                bk = Booking(
                    tenant_id=tid,
                    property_id=prop.id,
                    guest_id=g.id,
                    rate_plan_id=rp.id,
                    status="pending",
                    source="test",
                    total_amount=Decimal("88.00"),
                )
                session.add(bk)
                await session.flush()
                bid = bk.id
                for n in nights:
                    session.add(
                        Rate(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            rate_plan_id=rp.id,
                            date=n,
                            price=Decimal("44.00"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tid,
                            room_type_id=rt.id,
                            date=n,
                            total_rooms=10,
                            booked_rooms=1,
                            blocked_rooms=0,
                        ),
                    )
                    session.add(
                        BookingLine(
                            tenant_id=tid,
                            booking_id=bid,
                            date=n,
                            room_type_id=rt.id,
                            room_id=None,
                            price_for_date=Decimal("44.00"),
                        ),
                    )
        return {
            "tenant_id": tid,
            "user_id": uid,
            "booking_id": bid,
        }

    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed())


def test_patch_confirmed_to_cancel_returns_204(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "cancelled"})
    assert r.status_code == 204


def test_patch_confirmed_no_show_returns_204(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "no_show"})
    assert r.status_code == 204




def test_patch_checked_in_no_show_forbidden_returns_409(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "no_show"})
    assert r.status_code == 409


def test_patch_confirmed_to_checked_out_direct_forbidden_returns_409(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "checked_out"})
    assert r.status_code == 409


def test_patch_idempotent_confirmed_returns_204(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "confirmed"})
    assert r.status_code == 204


def test_patch_unknown_target_status_returns_409(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "refunded_bad"})
    assert r.status_code == 409


def test_patch_pending_to_confirmed_returns_204(
    client,
    booking_pending_confirm_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    tid = booking_pending_confirm_ctx["tenant_id"]
    uid = booking_pending_confirm_ctx["user_id"]
    bid = booking_pending_confirm_ctx["booking_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"status": "confirmed"})
    assert r.status_code == 204
