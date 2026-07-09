"""RLS isolation on tables added after core domain (folio categories, email, stripe, channex)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.stripe_secrets import encrypt_stripe_account_id
from app.integrations.channex.crypto import encrypt_channex_api_key
from app.models.billing.stripe_charge import StripeCharge
from app.models.billing.stripe_connection import StripeConnection
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_charge_category import FolioChargeCategory
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.notifications.email_log import EmailLog
from app.models.notifications.email_settings import EmailSettings
from app.models.rates.availability_ledger import AvailabilityLedger
from tests.db_seed import disable_row_security_for_test_seed


@pytest.fixture
def tenant_isolation_extended_scenario(db_engine: object) -> dict:
    """Two tenants; tenant A has booking + newer-table rows for RLS checks."""

    async def _seed() -> dict:
        tenant_a = uuid4()
        tenant_b = uuid4()
        folio_tx_id = uuid4()
        charge_id = uuid4()
        category_id = uuid4()
        link_id = uuid4()
        settings = get_settings()
        enc_stripe = encrypt_stripe_account_id(settings, "acct_iso_test")
        enc_channex = encrypt_channex_api_key(settings, "iso-test-channex-key")
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
                    night_d = date(2026, 3, 1) + timedelta(days=i)
                    session.add(
                        BookingLine(
                            tenant_id=tenant_a,
                            booking_id=booking.id,
                            date=night_d,
                            room_type_id=room_type.id,
                            room_id=None,
                            price_for_date=Decimal("33.34"),
                        ),
                    )
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tenant_a,
                            room_type_id=room_type.id,
                            date=night_d,
                            total_rooms=10,
                            booked_rooms=1,
                            blocked_rooms=0,
                        ),
                    )
                session.add(
                    FolioChargeCategory(
                        id=category_id,
                        tenant_id=tenant_a,
                        code="iso_test_cat",
                        label="Isolation Test",
                        is_builtin=False,
                        is_active=True,
                        sort_order=99,
                    ),
                )
                session.add(
                    EmailSettings(
                        tenant_id=tenant_a,
                        property_id=prop.id,
                        sender_name="Hotel A",
                        reply_to="front@a.example.com",
                        locale="en",
                    ),
                )
                session.add(
                    EmailLog(
                        tenant_id=tenant_a,
                        property_id=prop.id,
                        booking_id=booking.id,
                        to_address="guest@a.example.com",
                        template_name="checkin_reminder",
                        subject="Check-in reminder",
                        status="sent",
                        resend_id="msg_iso_test",
                    ),
                )
                session.add(
                    StripeConnection(
                        tenant_id=tenant_a,
                        property_id=prop.id,
                        stripe_account_id=enc_stripe,
                        livemode=False,
                        connected_at=datetime.now(UTC),
                    ),
                )
                session.add(
                    FolioTransaction(
                        id=folio_tx_id,
                        tenant_id=tenant_a,
                        booking_id=booking.id,
                        transaction_type="Payment",
                        amount=Decimal("50.00"),
                        payment_method="card",
                        description="Test payment",
                        category="payment",
                    ),
                )
                await session.flush()
                session.add(
                    StripeCharge(
                        id=charge_id,
                        tenant_id=tenant_a,
                        property_id=prop.id,
                        booking_id=booking.id,
                        folio_tx_id=folio_tx_id,
                        stripe_charge_id="ch_iso_test",
                        amount=Decimal("50.00"),
                        currency="usd",
                        status="succeeded",
                    ),
                )
                session.add(
                    ChannexPropertyLink(
                        id=link_id,
                        tenant_id=tenant_a,
                        property_id=prop.id,
                        channex_property_id=str(uuid4()),
                        channex_api_key=enc_channex,
                        channex_env="staging",
                        status="active",
                        connected_at=datetime.now(UTC),
                    ),
                )

        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "booking_id": booking.id,
            "property_id": prop.id,
            "guest_id": guest.id,
            "folio_category_id": category_id,
            "folio_category_code": "iso_test_cat",
            "stripe_charge_id": charge_id,
            "channex_link_id": link_id,
        }

    return asyncio.run(_seed())


def test_tenant_b_cannot_see_tenant_a_folio_category(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    code = tenant_isolation_extended_scenario["folio_category_code"]
    r = client.get("/folio-categories", headers=auth_headers(tenant_b, role="owner"))
    assert r.status_code == 200
    codes = {row["code"] for row in r.json()}
    assert code not in codes


def test_tenant_b_cannot_read_tenant_a_email_settings(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    pid: UUID = tenant_isolation_extended_scenario["property_id"]
    r = client.get(
        f"/properties/{pid}/email-settings",
        headers=auth_headers(tenant_b, role="owner"),
    )
    assert r.status_code == 404


def test_tenant_b_cannot_read_tenant_a_booking_email_logs(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    bid: UUID = tenant_isolation_extended_scenario["booking_id"]
    r = client.get(
        f"/bookings/{bid}/email-logs",
        headers=auth_headers(tenant_b, role="owner"),
    )
    assert r.status_code == 404


def test_tenant_b_cannot_see_tenant_a_stripe_status(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    pid: UUID = tenant_isolation_extended_scenario["property_id"]
    r = client.get(
        f"/properties/{pid}/stripe/status",
        headers=auth_headers(tenant_b, role="owner"),
    )
    assert r.status_code == 404


def test_tenant_b_cannot_list_tenant_a_stripe_charges(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    bid: UUID = tenant_isolation_extended_scenario["booking_id"]
    r = client.get(
        f"/bookings/{bid}/stripe/charges",
        headers=auth_headers(tenant_b, role="owner"),
    )
    assert r.status_code == 404


def test_tenant_b_cannot_see_tenant_a_channex_status(
    client,
    tenant_isolation_extended_scenario: dict,
    auth_headers,
) -> None:
    tenant_b: UUID = tenant_isolation_extended_scenario["tenant_b"]
    pid: UUID = tenant_isolation_extended_scenario["property_id"]
    r = client.get(
        "/channex/status",
        headers=auth_headers(tenant_b, role="owner"),
        params={"property_id": str(pid)},
    )
    assert r.status_code == 404
