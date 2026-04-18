"""Stripe Payments (Phase 3): service + HTTP + mocks."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import stripe
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import clear_settings_cache, get_settings
from app.core.security import hash_password
from app.core.stripe_secrets import encrypt_stripe_account_id
from app.models.auth.user import User
from app.models.billing.stripe_charge import StripeCharge
from app.models.billing.stripe_connection import StripeConnection
from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from tests.db_seed import disable_row_security_for_test_seed


def _pm_card_retrieve() -> SimpleNamespace:
    card = SimpleNamespace(last4="4242", brand="visa", exp_month=12, exp_year=2030)
    return SimpleNamespace(card=card)


def _seed_stripe_payment_scenario(db_engine: object) -> dict[str, UUID]:
    """Tenant + owner + property + StripeConnection + guest + booking.

    Uses a disposable async engine per call so asyncio.run does not bind the
    shared ``db_engine`` fixture to a closed event loop (see channex webhook tests).
    """

    _ = db_engine  # fixture keeps DATABASE_URL in env; avoid sharing pool across loops
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL required")

    tenant_id = uuid4()
    owner_id = uuid4()
    clear_settings_cache()
    settings = get_settings()
    eng = create_async_engine(url)

    async def _inner() -> dict[str, UUID]:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
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
                        name="StripePayTenant",
                        billing_email="sp@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email=f"o{owner_id.hex[:8]}@example.com",
                        password_hash=hash_password("x"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                await session.flush()
                prop = Property(
                    tenant_id=tenant_id,
                    name="Stripe Hotel",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                guest = Guest(
                    tenant_id=tenant_id,
                    first_name="Sam",
                    last_name="Guest",
                    email="sam@g.example.com",
                    phone="+1",
                )
                session.add(guest)
                await session.flush()
                booking = Booking(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    guest_id=guest.id,
                    status="confirmed",
                    source="test",
                    total_amount=Decimal("500.00"),
                )
                session.add(booking)
                await session.flush()
                enc = encrypt_stripe_account_id(settings, "acct_test_connected_openpms")
                session.add(
                    StripeConnection(
                        tenant_id=tenant_id,
                        property_id=prop.id,
                        stripe_account_id=enc,
                        livemode=False,
                        connected_at=datetime.now(UTC),
                        disconnected_at=None,
                    ),
                )
                await session.flush()
                return {
                    "tenant_id": tenant_id,
                    "owner_id": owner_id,
                    "property_id": prop.id,
                    "booking_id": booking.id,
                }

    try:
        return asyncio.run(_inner())
    finally:
        asyncio.run(eng.dispose())
        clear_settings_cache()


def test_save_payment_method_success(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        r = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={
                "stripe_pm_id": "pm_test_save_1",
                "booking_id": None,
                "label": "Virtual card",
            },
        )
    assert r.status_code == 201
    data = r.json()
    assert data["card_last4"] == "4242"
    assert data["card_brand"] == "visa"
    assert data["stripe_pm_id"] == "pm_test_save_1"


def test_list_payment_methods_and_booking_filter(
    client, db_engine, auth_headers_user
) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        r1 = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_list_a", "booking_id": str(bid), "label": None},
        )
        assert r1.status_code == 201
        r2 = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_list_b", "booking_id": None, "label": None},
        )
        assert r2.status_code == 201
    r_all = client.get(f"/properties/{pid}/stripe/payment-methods", headers=h)
    assert r_all.status_code == 200
    assert len(r_all.json()) == 2
    r_f = client.get(
        f"/properties/{pid}/stripe/payment-methods",
        headers=h,
        params={"booking_id": str(bid)},
    )
    assert r_f.status_code == 200
    assert len(r_f.json()) == 1
    assert r_f.json()[0]["stripe_pm_id"] == "pm_list_a"


def test_delete_payment_method_twice(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        created = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_del_1", "booking_id": None, "label": None},
        )
    assert created.status_code == 201
    pm_row_id = created.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.detach",
        return_value=SimpleNamespace(id="pm_del_1"),
    ):
        d1 = client.delete(f"/stripe/payment-methods/{pm_row_id}", headers=h)
    assert d1.status_code == 204
    r = client.get(f"/properties/{pid}/stripe/payment-methods", headers=h)
    assert r.json() == []
    d2 = client.delete(f"/stripe/payment-methods/{pm_row_id}", headers=h)
    assert d2.status_code == 404


def test_charge_success_folio_stripe_source(
    client, db_engine, auth_headers_user
) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_ch_ok", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    pi = SimpleNamespace(id="pi_test_ok_1")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=pi,
    ):
        r = client.post(
            f"/bookings/{bid}/stripe/charge",
            headers=h,
            json={"stripe_pm_id": pm_id, "amount": "100.00", "label": "Room payment"},
        )
    assert r.status_code == 201
    assert r.json()["status"] == "succeeded"
    assert r.json()["stripe_charge_id"] == "pi_test_ok_1"

    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert url
    eng = create_async_engine(url)
    try:

        async def _check_folio() -> None:
            factory = async_sessionmaker(
                eng, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                rows = (
                    (
                        await session.execute(
                            select(FolioTransaction).where(
                                FolioTransaction.tenant_id == tid,
                                FolioTransaction.booking_id == bid,
                            ),
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].source_channel == "stripe"
                assert rows[0].transaction_type == "Payment"

        asyncio.run(_check_folio())
    finally:
        asyncio.run(eng.dispose())


def test_charge_stripe_error_no_folio(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_ch_fail", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        side_effect=stripe.StripeError("card declined"),
    ):
        r = client.post(
            f"/bookings/{bid}/stripe/charge",
            headers=h,
            json={"stripe_pm_id": pm_id, "amount": "50.00", "label": None},
        )
    assert r.status_code == 422

    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert url
    eng = create_async_engine(url)
    try:

        async def _check() -> None:
            factory = async_sessionmaker(
                eng, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tid)},
                )
                folio_n = (
                    (
                        await session.execute(
                            select(FolioTransaction).where(
                                FolioTransaction.booking_id == bid
                            ),
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(folio_n) == 0
                ch = (
                    (
                        await session.execute(
                            select(StripeCharge).where(StripeCharge.booking_id == bid),
                        )
                    )
                    .scalars()
                    .first()
                )
                assert ch is not None
                assert ch.status == "failed"

        asyncio.run(_check())
    finally:
        asyncio.run(eng.dispose())


def test_charge_unknown_pm_row(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        f"/bookings/{bid}/stripe/charge",
        headers=h,
        json={"stripe_pm_id": str(uuid4()), "amount": "10.00", "label": None},
    )
    assert r.status_code == 422


def test_partial_and_full_refund_and_repeat(
    client,
    db_engine,
    auth_headers_user,
) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario_a = _seed_stripe_payment_scenario(db_engine)
    tid_a = scenario_a["tenant_id"]
    oid_a = scenario_a["owner_id"]
    pid_a = scenario_a["property_id"]
    bid_a = scenario_a["booking_id"]
    h_owner = auth_headers_user(tid_a, oid_a, role="owner")

    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid_a}/stripe/payment-methods",
            headers=h_owner,
            json={"stripe_pm_id": "pm_ref_partial", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    pi = SimpleNamespace(id="pi_ref_partial")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=pi,
    ):
        ch_r = client.post(
            f"/bookings/{bid_a}/stripe/charge",
            headers=h_owner,
            json={"stripe_pm_id": pm_id, "amount": "100.00", "label": None},
        )
    charge_row_id = ch_r.json()["id"]

    with patch(
        "app.services.stripe_payment_service.stripe.Refund.create",
        return_value=SimpleNamespace(id="re_1"),
    ):
        pr = client.post(
            f"/bookings/{bid_a}/stripe/refund",
            headers=h_owner,
            json={"stripe_charge_id": charge_row_id, "amount": "40.00"},
        )
    assert pr.status_code == 200
    assert pr.json()["status"] == "partial_refund"

    with patch(
        "app.services.stripe_payment_service.stripe.Refund.create",
        return_value=SimpleNamespace(id="re_2"),
    ):
        pr2 = client.post(
            f"/bookings/{bid_a}/stripe/refund",
            headers=h_owner,
            json={"stripe_charge_id": charge_row_id, "amount": "30.00"},
        )
    assert pr2.status_code == 422
    assert "only one refund" in pr2.json()["detail"].lower()

    scenario_b = _seed_stripe_payment_scenario(db_engine)
    tid_b = scenario_b["tenant_id"]
    oid_b = scenario_b["owner_id"]
    pid_b = scenario_b["property_id"]
    bid_b = scenario_b["booking_id"]
    h_b = auth_headers_user(tid_b, oid_b, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm2 = client.post(
            f"/properties/{pid_b}/stripe/payment-methods",
            headers=h_b,
            json={"stripe_pm_id": "pm_ref_full", "booking_id": None, "label": None},
        )
    pm2_id = pm2.json()["id"]
    pi2 = SimpleNamespace(id="pi_ref_full")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=pi2,
    ):
        ch2 = client.post(
            f"/bookings/{bid_b}/stripe/charge",
            headers=h_b,
            json={"stripe_pm_id": pm2_id, "amount": "80.00", "label": None},
        )
    cid2 = ch2.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.Refund.create",
        return_value=SimpleNamespace(id="re_full"),
    ):
        fr = client.post(
            f"/bookings/{bid_b}/stripe/refund",
            headers=h_b,
            json={"stripe_charge_id": cid2, "amount": None},
        )
    assert fr.status_code == 200
    assert fr.json()["status"] == "refunded"

    with patch(
        "app.services.stripe_payment_service.stripe.Refund.create",
        return_value=SimpleNamespace(id="re_full2"),
    ):
        fr2 = client.post(
            f"/bookings/{bid_b}/stripe/refund",
            headers=h_b,
            json={"stripe_charge_id": cid2, "amount": None},
        )
    assert fr2.status_code == 422


def test_refund_amount_exceeds_charge(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_ref_ex", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=SimpleNamespace(id="pi_ex"),
    ):
        ch = client.post(
            f"/bookings/{bid}/stripe/charge",
            headers=h,
            json={"stripe_pm_id": pm_id, "amount": "25.00", "label": None},
        )
    cid = ch.json()["id"]
    r = client.post(
        f"/bookings/{bid}/stripe/refund",
        headers=h,
        json={"stripe_charge_id": cid, "amount": "100.00"},
    )
    assert r.status_code == 422


def test_charge_other_tenant_booking_404(
    client,
    db_engine,
    auth_headers_user,
    tenant_isolation_booking_scenario: dict,
) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    tid_b: UUID = tenant_isolation_booking_scenario["tenant_b"]  # type: ignore[assignment]
    bid_a: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    oid_b = uuid4()

    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert url
    eng = create_async_engine(url)
    try:

        async def _add_owner_b() -> None:
            factory = async_sessionmaker(
                eng, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                async with session.begin():
                    await disable_row_security_for_test_seed(session)
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                        ),
                        {"tid": str(tid_b)},
                    )
                    session.add(
                        User(
                            id=oid_b,
                            tenant_id=tid_b,
                            email=f"iso{oid_b.hex[:6]}@example.com",
                            password_hash=hash_password("x"),
                            full_name="B Owner",
                            role="owner",
                        ),
                    )

        asyncio.run(_add_owner_b())
    finally:
        asyncio.run(eng.dispose())

    h_b = auth_headers_user(tid_b, oid_b, role="owner")
    r = client.post(
        f"/bookings/{bid_a}/stripe/charge",
        headers=h_b,
        json={"stripe_pm_id": str(uuid4()), "amount": "10.00", "label": None},
    )
    assert r.status_code == 404


def test_refund_requires_owner_not_manager(
    client, db_engine, auth_headers_user
) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    mid = uuid4()

    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    assert url
    eng = create_async_engine(url)
    try:

        async def _add_manager() -> None:
            factory = async_sessionmaker(
                eng, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                async with session.begin():
                    await disable_row_security_for_test_seed(session)
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                        ),
                        {"tid": str(tid)},
                    )
                    session.add(
                        User(
                            id=mid,
                            tenant_id=tid,
                            email=f"m{mid.hex[:8]}@example.com",
                            password_hash=hash_password("x"),
                            full_name="Mgr",
                            role="manager",
                        ),
                    )

        asyncio.run(_add_manager())
    finally:
        asyncio.run(eng.dispose())

    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=auth_headers_user(tid, oid, role="owner"),
            json={"stripe_pm_id": "pm_mgr", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=SimpleNamespace(id="pi_mgr"),
    ):
        ch = client.post(
            f"/bookings/{bid}/stripe/charge",
            headers=auth_headers_user(tid, mid, role="manager"),
            json={"stripe_pm_id": pm_id, "amount": "15.00", "label": None},
        )
    assert ch.status_code == 201
    cid = ch.json()["id"]
    r = client.post(
        f"/bookings/{bid}/stripe/refund",
        headers=auth_headers_user(tid, mid, role="manager"),
        json={"stripe_charge_id": cid, "amount": None},
    )
    assert r.status_code == 403


def test_get_booking_charges_lists_rows(client, db_engine, auth_headers_user) -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    scenario = _seed_stripe_payment_scenario(db_engine)
    tid = scenario["tenant_id"]
    oid = scenario["owner_id"]
    pid = scenario["property_id"]
    bid = scenario["booking_id"]
    h = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentMethod.retrieve",
        return_value=_pm_card_retrieve(),
    ):
        pm = client.post(
            f"/properties/{pid}/stripe/payment-methods",
            headers=h,
            json={"stripe_pm_id": "pm_list_ch", "booking_id": None, "label": None},
        )
    pm_id = pm.json()["id"]
    with patch(
        "app.services.stripe_payment_service.stripe.PaymentIntent.create",
        return_value=SimpleNamespace(id="pi_lc"),
    ):
        client.post(
            f"/bookings/{bid}/stripe/charge",
            headers=h,
            json={"stripe_pm_id": pm_id, "amount": "20.00", "label": None},
        )
    r = client.get(f"/bookings/{bid}/stripe/charges", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "succeeded"
