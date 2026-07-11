"""SECURITY DEFINER lookup_stripe_charge_for_webhook — tenant resolution by PI id."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.billing.stripe_charge import StripeCharge
from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from tests.db_seed import disable_row_security_for_test_seed


def _seed_two_tenant_charges() -> dict:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    eng = create_async_engine(url)

    async def _inner() -> dict:
        tenant_a = uuid4()
        tenant_b = uuid4()
        charge_a = uuid4()
        charge_b = uuid4()
        pi_a = f"pi_lookup_a_{uuid4().hex[:12]}"
        pi_b = f"pi_lookup_b_{uuid4().hex[:12]}"
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                for tid, label in ((tenant_a, "LookupA"), (tenant_b, "LookupB")):
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
                    prop = Property(
                        tenant_id=tid,
                        name=f"{label} Hotel",
                        timezone="UTC",
                        currency="USD",
                        checkin_time=time(14, 0),
                        checkout_time=time(11, 0),
                    )
                    session.add(prop)
                    await session.flush()
                    guest = Guest(
                        tenant_id=tid,
                        first_name="G",
                        last_name=label,
                        email=f"g@{label.lower()}.example.com",
                        phone="+1",
                    )
                    session.add(guest)
                    await session.flush()
                    booking = Booking(
                        tenant_id=tid,
                        property_id=prop.id,
                        guest_id=guest.id,
                        status="confirmed",
                        source="test",
                        total_amount=Decimal("10.00"),
                    )
                    session.add(booking)
                    await session.flush()
                    cid = charge_a if tid == tenant_a else charge_b
                    pi = pi_a if tid == tenant_a else pi_b
                    session.add(
                        StripeCharge(
                            id=cid,
                            tenant_id=tid,
                            property_id=prop.id,
                            booking_id=booking.id,
                            folio_tx_id=None,
                            stripe_charge_id=pi,
                            amount=Decimal("10.00"),
                            currency="usd",
                            status="succeeded",
                        ),
                    )
        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "charge_a": charge_a,
            "charge_b": charge_b,
            "pi_a": pi_a,
            "pi_b": pi_b,
        }

    try:
        return asyncio.run(_inner())
    finally:
        asyncio.run(eng.dispose())


def test_lookup_stripe_charge_for_webhook_resolves_correct_tenant() -> None:
    scenario = _seed_two_tenant_charges()
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert url
    eng = create_async_engine(url)

    async def _check() -> None:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            # Function bypasses RLS; call without app.tenant_id set.
            row_a = (
                await session.execute(
                    text(
                        "SELECT tenant_id, charge_id "
                        "FROM lookup_stripe_charge_for_webhook(:pi)",
                    ),
                    {"pi": scenario["pi_a"]},
                )
            ).first()
            assert row_a is not None
            assert row_a[0] == scenario["tenant_a"]
            assert row_a[1] == scenario["charge_a"]
            # Only the two documented columns are returned.
            assert len(row_a) == 2

            row_b = (
                await session.execute(
                    text(
                        "SELECT tenant_id, charge_id "
                        "FROM lookup_stripe_charge_for_webhook(:pi)",
                    ),
                    {"pi": scenario["pi_b"]},
                )
            ).first()
            assert row_b is not None
            assert row_b[0] == scenario["tenant_b"]
            assert row_b[1] == scenario["charge_b"]

            missing = (
                await session.execute(
                    text(
                        "SELECT tenant_id, charge_id "
                        "FROM lookup_stripe_charge_for_webhook(:pi)",
                    ),
                    {"pi": f"pi_missing_{uuid4().hex}"},
                )
            ).first()
            assert missing is None

    try:
        asyncio.run(_check())
    finally:
        asyncio.run(eng.dispose())
