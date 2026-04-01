"""Folio balance, POST charge/payment, storno, checkout balance warning."""

from __future__ import annotations

from uuid import UUID


def test_get_folio_lists_transactions_and_balance(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    r = client.get(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
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
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
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
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
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
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
    ).json()
    assert after["balance"] == "150.00"
    assert len(after["transactions"]) == 3


def test_patch_checked_out_warns_when_folio_not_zero(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    r = client.patch(
        f"/bookings/{booking_id}",
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
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
        headers=auth_headers(
            tenant_id, user_id=user_id, role="receptionist"
        ),
        json={"status": "checked_out"},
    )
    assert r.status_code == 204
    assert r.text == ""
