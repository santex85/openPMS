"""Folio charge categories HTTP API."""

from __future__ import annotations

from uuid import UUID


def test_list_folio_categories_returns_builtins(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    r = client.get(
        "/folio-categories",
        headers=auth_headers(tenant_id, user_id=user_id, role="receptionist"),
    )
    assert r.status_code == 200
    rows = r.json()
    codes = {item["code"] for item in rows}
    assert "room_charge" in codes
    assert "misc" in codes
    assert "payment" not in codes


def test_create_patch_delete_custom_category(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    cr = client.post(
        "/folio-categories",
        headers=auth_headers(tenant_id, user_id=user_id, role="owner"),
        json={
            "code": "laundry_svc",
            "label": "Laundry",
            "sort_order": 100,
            "is_active": True,
        },
    )
    assert cr.status_code == 201
    assert cr.json()["code"] == "laundry_svc"
    assert cr.json()["is_builtin"] is False

    pr = client.patch(
        "/folio-categories/laundry_svc",
        headers=auth_headers(tenant_id, user_id=user_id, role="owner"),
        json={"label": "Laundry & pressing"},
    )
    assert pr.status_code == 200
    assert pr.json()["label"] == "Laundry & pressing"

    dr = client.delete(
        "/folio-categories/laundry_svc",
        headers=auth_headers(tenant_id, user_id=user_id, role="owner"),
    )
    assert dr.status_code == 204


def test_receptionist_cannot_create_category(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    r = client.post(
        "/folio-categories",
        headers=auth_headers(tenant_id, user_id=user_id, role="receptionist"),
        json={"code": "xtra", "label": "Extra"},
    )
    assert r.status_code == 403


def test_cannot_delete_builtin(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    r = client.delete(
        "/folio-categories/spa",
        headers=auth_headers(tenant_id, user_id=user_id, role="owner"),
    )
    assert r.status_code == 400


def test_post_folio_charge_uses_custom_category(
    client,
    folio_scenario: dict,
    auth_headers,
    auth_headers_user,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    assert (
        client.post(
            "/folio-categories",
            headers=auth_headers(tenant_id, user_id=user_id, role="owner"),
            json={"code": "boat_trip", "label": "Boat trip"},
        ).status_code
        == 201
    )
    mr = client.post(
        f"/bookings/{booking_id}/folio",
        headers=auth_headers_user(tenant_id, user_id),
        json={
            "entry_type": "charge",
            "amount": "12.00",
            "category": "boat_trip",
            "description": "Tour",
        },
    )
    assert mr.status_code == 201
    assert mr.json()["category"] == "boat_trip"
