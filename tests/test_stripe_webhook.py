"""POST /webhooks/stripe — signature, refund reconciliation, dispute, idempotency."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import stripe
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import clear_settings_cache
from app.models.billing.stripe_charge import StripeCharge
from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from tests.db_seed import disable_row_security_for_test_seed

_SECRET = "whsec_pytest_stripe_secret"
_REFUND_DESC = "Stripe refund (webhook)"


@pytest.fixture
def stripe_charge_scenario(db_engine: object) -> dict:
    """One tenant with a succeeded stripe charge (amount 50.00) and its folio payment.

    Uses a disposable async engine + single asyncio.run so it never binds the shared
    ``db_engine`` fixture to a closed loop (mirrors test_stripe_payments.py).
    """

    _ = db_engine  # keeps DATABASE_URL skip behavior; avoid sharing pool across loops
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")

    tenant_id = uuid4()
    pi_id = f"pi_test_{uuid4().hex}"
    eng = create_async_engine(url)

    async def _inner() -> dict:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
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
                return {
                    "tenant_id": tenant_id,
                    "charge_id": charge.id,
                    "booking_id": booking.id,
                    "pi_id": pi_id,
                }

    try:
        return asyncio.run(_inner())
    finally:
        asyncio.run(eng.dispose())


def _charge_status(client, auth_headers, tenant_id: UUID, booking_id: UUID) -> str:
    r = client.get(
        f"/bookings/{booking_id}/stripe/charges",
        headers=auth_headers(tenant_id, role="owner"),
    )
    assert r.status_code == 200, r.text
    charges = r.json()
    assert len(charges) >= 1
    return charges[0]["status"]


def _refund_folio_lines(
    client, auth_headers, tenant_id: UUID, booking_id: UUID
) -> tuple[int, Decimal]:
    r = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(tenant_id, role="owner"),
    )
    assert r.status_code == 200, r.text
    rows = [t for t in r.json()["transactions"] if t.get("description") == _REFUND_DESC]
    total = sum((Decimal(str(t["amount"])) for t in rows), Decimal("0"))
    return len(rows), total


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
    auth_headers,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
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
    assert _charge_status(client, auth_headers, tenant_id, booking_id) == "refunded"
    n, total = _refund_folio_lines(client, auth_headers, tenant_id, booking_id)
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
    n2, total2 = _refund_folio_lines(client, auth_headers, tenant_id, booking_id)
    assert n2 == 1
    assert total2 == Decimal("-50.00")


def test_webhook_partial_refund_sets_partial_status(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
    auth_headers,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
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
    assert (
        _charge_status(client, auth_headers, tenant_id, booking_id) == "partial_refund"
    )
    n, total = _refund_folio_lines(client, auth_headers, tenant_id, booking_id)
    assert n == 1
    assert total == Decimal("-25.00")


def test_webhook_refund_without_amount_defaults_to_full(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
    auth_headers,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
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
    assert _charge_status(client, auth_headers, tenant_id, booking_id) == "refunded"
    n, total = _refund_folio_lines(client, auth_headers, tenant_id, booking_id)
    assert n == 1
    assert total == Decimal("-50.00")


def test_webhook_over_refund_is_clamped_to_charge_total(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
    auth_headers,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
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
    assert _charge_status(client, auth_headers, tenant_id, booking_id) == "refunded"
    _n, total = _refund_folio_lines(client, auth_headers, tenant_id, booking_id)
    assert total == Decimal("-50.00")


def test_webhook_dispute_leaves_charge_status(
    client,
    monkeypatch: pytest.MonkeyPatch,
    stripe_charge_scenario: dict,
    auth_headers,
) -> None:
    _with_secret(monkeypatch)
    tenant_id = stripe_charge_scenario["tenant_id"]
    booking_id = stripe_charge_scenario["booking_id"]
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
    # Dispute is recorded (audit + Sentry) but does not change charge status.
    assert _charge_status(client, auth_headers, tenant_id, booking_id) == "succeeded"
