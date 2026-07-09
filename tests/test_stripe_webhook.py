"""POST /webhooks/stripe — signature, refund reconciliation, dispute, idempotency."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import time
from decimal import Decimal
from typing import TypeVar
from unittest.mock import patch
from uuid import uuid4

import pytest
import stripe
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import clear_settings_cache
from app.models.audit.audit_log import AuditLog
from app.models.billing.stripe_charge import StripeCharge
from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from tests.db_seed import disable_row_security_for_test_seed

_SECRET = "whsec_pytest_stripe_secret"

T = TypeVar("T")


def _run_db(fn: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    """Run a DB coroutine on a fresh engine+loop (safe to call repeatedly per test)."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")

    async def _wrap() -> T:
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            return await fn(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_wrap())


@pytest.fixture
def stripe_charge_scenario() -> dict:
    """One tenant with a succeeded stripe charge (amount 50.00) and its folio payment."""

    async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict:
        tenant_id = uuid4()
        pi_id = f"pi_test_{uuid4().hex}"
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
                        name="StripeHookTenant",
                        billing_email="hook@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tenant_id,
                    name="Hook Property",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                guest = Guest(
                    tenant_id=tenant_id,
                    first_name="H",
                    last_name="Guest",
                    email="hg@example.com",
                    phone="+10000000009",
                )
                session.add(guest)
                await session.flush()
                booking = Booking(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=guest.id,
                    status="confirmed",
                    source="test",
                    total_amount=Decimal("50.00"),
                )
                session.add(booking)
                await session.flush()
                folio = FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    transaction_type="Payment",
                    amount=Decimal("50.00"),
                    payment_method="stripe",
                    description="Stripe card payment",
                    category="payment",
                    source_channel="stripe",
                )
                session.add(folio)
                await session.flush()
                charge = StripeCharge(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    booking_id=booking.id,
                    folio_tx_id=folio.id,
                    stripe_charge_id=pi_id,
                    amount=Decimal("50.00"),
                    currency="USD",
                    status="succeeded",
                )
                session.add(charge)
                await session.flush()
                charge_id = charge.id
                booking_id = booking.id

        return {
            "tenant_id": tenant_id,
            "charge_id": charge_id,
            "booking_id": booking_id,
            "pi_id": pi_id,
        }

    return _run_db(_seed)


def _read_charge_status(tenant_id, charge_id) -> str:
    async def _fn(factory: async_sessionmaker[AsyncSession]) -> str:
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                row = await session.scalar(
                    select(StripeCharge).where(StripeCharge.id == charge_id)
                )
                return row.status

    return _run_db(_fn)


def _count_stripe_folio(booking_id) -> tuple[int, Decimal]:
    async def _fn(factory: async_sessionmaker[AsyncSession]) -> tuple[int, Decimal]:
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                rows = (
                    (
                        await session.execute(
                            select(FolioTransaction).where(
                                FolioTransaction.booking_id == booking_id,
                                FolioTransaction.description
                                == "Stripe refund (webhook)",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                total = sum((r.amount for r in rows), Decimal("0"))
                return len(rows), total

    return _run_db(_fn)


def _count_audit(entity_id, action: str) -> int:
    async def _fn(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                rows = (
                    (
                        await session.execute(
                            select(AuditLog).where(
                                AuditLog.entity_id == entity_id,
                                AuditLog.action == action,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return len(rows)

    return _run_db(_fn)


def _with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)
    clear_settings_cache()


def test_webhook_disabled_without_secret_returns_503(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    clear_settings_cache()
    r = client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=x"},
    )
    assert r.status_code == 503, r.text


def test_webhook_invalid_signature_returns_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    with patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe.error.SignatureVerificationError("bad", "sig"),
    ):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=bad"},
        )
    assert r.status_code == 400, r.text


def test_webhook_missing_signature_returns_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    r = client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 400, r.text


def test_webhook_unknown_event_returns_200_ignored(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    event = {"type": "payment_intent.created", "data": {"object": {}}}
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_unknown_payment_intent_returns_200_ignored(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": f"pi_missing_{uuid4().hex}"}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_refund_reconciles_and_is_idempotent(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    charge_id = stripe_charge_scenario["charge_id"]
    booking_id = stripe_charge_scenario["booking_id"]
    pi_id = stripe_charge_scenario["pi_id"]
    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": pi_id, "amount_refunded": 5000}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r1 = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r1.status_code == 200, r1.text
    assert _read_charge_status(tenant_id, charge_id) == "refunded"
    n, total = _count_stripe_folio(booking_id)
    assert n == 1
    assert total == Decimal("-50.00")

    # Redelivery: no second folio line, status stays refunded.
    with patch("stripe.Webhook.construct_event", return_value=event):
        r2 = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r2.status_code == 200, r2.text
    n2, total2 = _count_stripe_folio(booking_id)
    assert n2 == 1
    assert total2 == Decimal("-50.00")


def test_webhook_partial_refund_sets_partial_status(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    charge_id = stripe_charge_scenario["charge_id"]
    booking_id = stripe_charge_scenario["booking_id"]
    pi_id = stripe_charge_scenario["pi_id"]
    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": pi_id, "amount_refunded": 2500}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200, r.text
    assert _read_charge_status(tenant_id, charge_id) == "partial_refund"
    n, total = _count_stripe_folio(booking_id)
    assert n == 1
    assert total == Decimal("-25.00")


def test_webhook_refund_without_amount_defaults_to_full(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    charge_id = stripe_charge_scenario["charge_id"]
    booking_id = stripe_charge_scenario["booking_id"]
    pi_id = stripe_charge_scenario["pi_id"]
    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": pi_id}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200, r.text
    assert _read_charge_status(tenant_id, charge_id) == "refunded"
    n, total = _count_stripe_folio(booking_id)
    assert n == 1
    assert total == Decimal("-50.00")


def test_webhook_over_refund_is_clamped_to_charge_total(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    charge_id = stripe_charge_scenario["charge_id"]
    booking_id = stripe_charge_scenario["booking_id"]
    pi_id = stripe_charge_scenario["pi_id"]
    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": pi_id, "amount_refunded": 999999}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200, r.text
    assert _read_charge_status(tenant_id, charge_id) == "refunded"
    _n, total = _count_stripe_folio(booking_id)
    assert total == Decimal("-50.00")


def test_webhook_dispute_writes_audit(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    charge_id = stripe_charge_scenario["charge_id"]
    pi_id = stripe_charge_scenario["pi_id"]
    event = {
        "type": "charge.dispute.created",
        "data": {"object": {"id": "dp_test_1", "payment_intent": pi_id}},
    }
    with patch("stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=1,v1=x"},
        )
    assert r.status_code == 200, r.text
    assert _count_audit(charge_id, "stripe.dispute_created") == 1
    # Dispute does not change charge status.
    assert _read_charge_status(tenant_id, charge_id) == "succeeded"
