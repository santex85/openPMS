"""Property tax config service, API, and booking receipt (Phase 1)."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.billing.tax_config import TaxConfig, TaxMode
from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.services import tax_service
from app.services.booking_receipt_service import build_booking_receipt
from app.services.folio_service import FolioError, sum_booking_charge_amounts
from app.services.tax_service import calculate_property_tax, property_tax_summary_lines
from tests.db_seed import disable_row_security_for_test_seed


def test_calculate_property_tax_inclusive_example() -> None:
    cfg = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.inclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.07"),
    )
    bd = calculate_property_tax(Decimal("34200.00"), cfg)
    assert bd.tax_amount == Decimal("2237.38")
    assert bd.net_total == Decimal("31962.62")
    assert bd.gross_total == Decimal("34200.00")


def test_calculate_property_tax_exclusive_example() -> None:
    cfg = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.exclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.07"),
    )
    bd = calculate_property_tax(Decimal("34200.00"), cfg)
    assert bd.tax_amount == Decimal("2394.00")
    assert bd.gross_total == Decimal("36594.00")
    assert bd.net_total == Decimal("34200.00")


def test_calculate_property_tax_off() -> None:
    cfg = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.off,
        tax_name="None",
        tax_rate=Decimal("0.10"),
    )
    bd = calculate_property_tax(Decimal("100.00"), cfg)
    assert bd.tax_amount == Decimal("0.00")
    assert bd.gross_total == Decimal("100.00")
    assert bd.net_total == Decimal("100.00")


@pytest.mark.asyncio
async def test_sum_booking_charge_amounts_no_charges(db_engine) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid4()
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
                    name="SumChg",
                    billing_email="sc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="P",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            guest = Guest(
                tenant_id=tenant_id,
                first_name="G",
                last_name="H",
                email="gh@example.com",
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
                total_amount=Decimal("0.00"),
            )
            session.add(booking)
            await session.flush()
            booking_id = booking.id

    async with factory() as session:
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
            ),
            {"tid": str(tenant_id)},
        )
        total = await sum_booking_charge_amounts(session, tenant_id, booking_id)
        assert total == Decimal("0.00")


@pytest.mark.asyncio
async def test_build_booking_receipt_unknown_booking(db_engine) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid4()
    booking_id = uuid4()
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(FolioError, match="booking not found"):
                await build_booking_receipt(session, tenant_id, booking_id)


@pytest.mark.asyncio
async def test_tax_service_delete_when_missing_returns_false(db_engine) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid4()
    property_id = uuid4()
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            deleted = await tax_service.delete_tax_config(
                session, tenant_id, property_id
            )
            assert deleted is False
            cfg = await tax_service.get_tax_config(session, tenant_id, property_id)
            assert cfg is None


def test_property_tax_summary_lines() -> None:
    cfg_inc = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.inclusive,
        tax_name="VAT",
        tax_rate=Decimal("0.07"),
    )
    bd = calculate_property_tax(Decimal("100.00"), cfg_inc)
    lines_inc = property_tax_summary_lines(cfg_inc, bd)
    assert len(lines_inc) == 1
    assert "Includes VAT" in lines_inc[0]

    cfg_exc = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.exclusive,
        tax_name="GST",
        tax_rate=Decimal("0.05"),
    )
    bd_exc = calculate_property_tax(Decimal("200.00"), cfg_exc)
    lines_exc = property_tax_summary_lines(cfg_exc, bd_exc)
    assert lines_exc[0].startswith("GST")

    cfg_blank_name = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.inclusive,
        tax_name="   ",
        tax_rate=Decimal("0.1"),
    )
    bd_bn = calculate_property_tax(Decimal("50.00"), cfg_blank_name)
    assert "Tax" in property_tax_summary_lines(cfg_blank_name, bd_bn)[0]

    cfg_off = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.off,
        tax_name="X",
        tax_rate=Decimal("0"),
    )
    bd_off = calculate_property_tax(Decimal("10.00"), cfg_off)
    assert property_tax_summary_lines(cfg_off, bd_off) == []


def test_calculate_property_tax_rate_boundaries() -> None:
    cfg_inc = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.inclusive,
        tax_name="X",
        tax_rate=Decimal("0"),
    )
    bd0 = calculate_property_tax(Decimal("50.00"), cfg_inc)
    assert bd0.tax_amount == Decimal("0.00")

    cfg_exc = TaxConfig(
        tenant_id=uuid4(),
        property_id=uuid4(),
        tax_mode=TaxMode.exclusive,
        tax_name="X",
        tax_rate=Decimal("1"),
    )
    bd1 = calculate_property_tax(Decimal("100.00"), cfg_exc)
    assert bd1.tax_amount == Decimal("100.00")
    assert bd1.gross_total == Decimal("200.00")


async def _seed_minimal_property(
    url: str,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    user_role: str,
) -> UUID:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:

        async def _inner() -> UUID:
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
                            name="TaxSeed",
                            billing_email="seed@example.com",
                            status="active",
                        ),
                    )
                    await session.flush()
                    if user_id is not None:
                        session.add(
                            User(
                                id=user_id,
                                tenant_id=tenant_id,
                                email=f"u{user_id.hex[:8]}@example.com",
                                password_hash=hash_password("x"),
                                full_name="User",
                                role=user_role,
                            ),
                        )
                        await session.flush()
                    prop = Property(
                        tenant_id=tenant_id,
                        name="PS",
                        timezone="UTC",
                        currency="USD",
                        checkin_time=time(14, 0),
                        checkout_time=time(11, 0),
                    )
                    session.add(prop)
                    await session.flush()
                    return prop.id

        return await _inner()
    finally:
        await engine.dispose()


def test_put_get_delete_tax_config_api(client, auth_headers, auth_headers_user) -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    owner_id = uuid4()
    property_id = asyncio.run(
        _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=owner_id,
            user_role="owner",
        ),
    )

    h_owner = auth_headers_user(tenant_id, owner_id, role="owner")
    put = client.put(
        f"/properties/{property_id}/tax-config",
        headers=h_owner,
        json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["tax_mode"] == "inclusive"

    get_r = client.get(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers(tenant_id, role="manager"),
    )
    assert get_r.status_code == 200

    del_r = client.delete(
        f"/properties/{property_id}/tax-config",
        headers=h_owner,
    )
    assert del_r.status_code == 204

    get404 = client.get(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers(tenant_id, role="manager"),
    )
    assert get404.status_code == 404


def test_put_tax_config_twice_updates_row(client, auth_headers_user) -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    owner_id = uuid4()
    property_id = asyncio.run(
        _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=owner_id,
            user_role="owner",
        ),
    )
    h = auth_headers_user(tenant_id, owner_id, role="owner")
    assert (
        client.put(
            f"/properties/{property_id}/tax-config",
            headers=h,
            json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.05"},
        ).status_code
        == 200
    )
    r2 = client.put(
        f"/properties/{property_id}/tax-config",
        headers=h,
        json={"tax_mode": "exclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    assert r2.status_code == 200
    assert r2.json()["tax_mode"] == "exclusive"


def test_put_tax_config_invalid_rate_422(client, auth_headers_user) -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    owner_id = uuid4()
    property_id = asyncio.run(
        _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=owner_id,
            user_role="owner",
        ),
    )

    r = client.put(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers_user(tenant_id, owner_id, role="owner"),
        json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "1.5"},
    )
    assert r.status_code == 422


def test_manager_put_tax_config_403(client, auth_headers_user) -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    mgr_id = uuid4()
    property_id = asyncio.run(
        _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=mgr_id,
            user_role="manager",
        ),
    )

    r = client.put(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers_user(tenant_id, mgr_id, role="manager"),
        json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    assert r.status_code == 403


def test_delete_tax_config_missing_404(client, auth_headers_user) -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")

    tenant_id = uuid4()
    owner_id = uuid4()
    property_id = asyncio.run(
        _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=owner_id,
            user_role="owner",
        ),
    )

    r = client.delete(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers_user(tenant_id, owner_id, role="owner"),
    )
    assert r.status_code == 404


def test_tenant_b_cannot_read_tenant_a_tax_config(
    client,
    auth_headers,
    tenant_isolation_booking_scenario: dict,
) -> None:
    tid_a: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    tid_b: UUID = tenant_isolation_booking_scenario["tenant_b"]  # type: ignore[assignment]
    prop_a: UUID = tenant_isolation_booking_scenario["property_id"]  # type: ignore[assignment]

    put = client.put(
        f"/properties/{prop_a}/tax-config",
        headers=auth_headers(tid_a, role="owner"),
        json={"tax_mode": "exclusive", "tax_name": "GST", "tax_rate": "0.1"},
    )
    assert put.status_code == 200

    get_b = client.get(
        f"/properties/{prop_a}/tax-config",
        headers=auth_headers(tid_b, role="owner"),
    )
    assert get_b.status_code == 404


def test_receipt_without_tax_omits_tax_fields(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]

    r = client.get(
        f"/bookings/{booking_id}/receipt",
        headers=auth_headers(tenant_id, user_id=user_id, role="receptionist"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["charge_subtotal"] == "150.00"
    assert "tax_mode" not in data
    assert data.get("tax_summary_lines") in (None, [])


def test_receipt_inclusive_shows_tax_breakdown(
    client,
    folio_scenario: dict,
    auth_headers,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    property_id: UUID = folio_scenario["property_id"]  # type: ignore[assignment]

    pr = client.put(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers_user(tenant_id, user_id, role="receptionist"),
        json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    assert pr.status_code == 403

    pr2 = client.put(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers(tenant_id, role="owner"),
        json={"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    assert pr2.status_code == 200, pr2.text

    r = client.get(
        f"/bookings/{booking_id}/receipt",
        headers=auth_headers(tenant_id, user_id=user_id, role="receptionist"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["tax_mode"] == "inclusive"
    assert data["tax_breakdown"]["gross_total"] == "150.00"
    lines = data["tax_summary_lines"]
    assert len(lines) == 1
    assert "Includes VAT" in lines[0]
    assert "7.00%" in lines[0] or "7%" in lines[0]


def test_receipt_exclusive_increases_gross(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    property_id: UUID = folio_scenario["property_id"]  # type: ignore[assignment]

    client.put(
        f"/properties/{property_id}/tax-config",
        headers=auth_headers(tenant_id, role="owner"),
        json={"tax_mode": "exclusive", "tax_name": "VAT", "tax_rate": "0.07"},
    )
    r = client.get(
        f"/bookings/{booking_id}/receipt",
        headers=auth_headers(tenant_id, user_id=user_id, role="receptionist"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["tax_mode"] == "exclusive"
    assert data["tax_breakdown"]["net_total"] == "150.00"
    assert data["tax_breakdown"]["tax_amount"] == "10.50"
    assert data["tax_breakdown"]["gross_total"] == "160.50"
    assert "VAT" in data["tax_summary_lines"][0]
