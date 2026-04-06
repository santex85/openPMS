"""Folio storno (POST …/reverse; legacy DELETE) edge cases."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bookings.folio_transaction import FolioTransaction


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def _insert_zero_charge_tx(tenant_id: UUID, booking_id: UUID) -> UUID:
    url = _database_url()
    assert url
    tx_id = uuid4()
    engine = create_async_engine(url)

    async def _main() -> UUID:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    FolioTransaction(
                        id=tx_id,
                        tenant_id=tenant_id,
                        booking_id=booking_id,
                        transaction_type="Charge",
                        amount=Decimal("0.00"),
                        payment_method=None,
                        description="Zero edge case",
                        created_by=None,
                        category="minibar",
                    ),
                )
        await engine.dispose()
        return tx_id

    return asyncio.run(_main())


@pytest.fixture
def zero_amount_tx_id(folio_scenario: dict) -> tuple[UUID, UUID, UUID]:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tx_id = _insert_zero_charge_tx(tenant_id, booking_id)
    return tenant_id, booking_id, tx_id


def test_folio_reverse_unknown_transaction_404(
    client,
    folio_scenario: dict,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    fake_tx = uuid4()
    r = client.post(
        f"/bookings/{booking_id}/folio/{fake_tx}/reverse",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert r.status_code == 404


def test_folio_reverse_zero_amount_409(
    client,
    folio_scenario: dict,
    auth_headers_user,
    zero_amount_tx_id: tuple[UUID, UUID, UUID],
) -> None:
    tenant_id, booking_id, tx_id = zero_amount_tx_id
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    r = client.post(
        f"/bookings/{booking_id}/folio/{tx_id}/reverse",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert r.status_code == 409
    assert "zero" in r.json()["detail"].lower()


def test_folio_double_storno_second_call_still_201(
    client,
    folio_scenario: dict,
    auth_headers_user,
) -> None:
    """
    Current behaviour: reversing the same original transaction twice inserts two
    offsetting rows.
    """
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    mr = client.post(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers_user(tenant_id, user_id),
        json={
            "entry_type": "charge",
            "amount": "10.00",
            "category": "minibar",
            "description": "Coffee",
        },
    )
    assert mr.status_code == 201
    tx_id = mr.json()["id"]
    r1 = client.post(
        f"/bookings/{booking_id}/folio/{tx_id}/reverse",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/bookings/{booking_id}/folio/{tx_id}/reverse",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert r2.status_code == 201


def test_folio_reverse_deprecated_delete_returns_200(
    client,
    folio_scenario: dict,
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
            "amount": "5.00",
            "category": "minibar",
            "description": "Tea",
        },
    )
    assert mr.status_code == 201
    tx_id = mr.json()["id"]
    dr = client.delete(
        f"/bookings/{booking_id}/folio/{tx_id}",
        headers=auth_headers_user(tenant_id, user_id),
    )
    assert dr.status_code == 200
    assert "Reversal" in dr.json()["description"]
