"""Integration smoke tests: guests, housekeeping, api-keys, webhooks, audit read."""

from __future__ import annotations

from uuid import UUID

import pytest


def test_guests_search_and_create(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get("/guests", headers=h, params={"q": "nobody"})
    assert r.status_code == 200
    gj = r.json()
    assert gj["items"] == []
    assert gj["total"] == 0
    cr = client.post(
        "/guests",
        headers=h,
        json={
            "first_name": "Sam",
            "last_name": "Smoke",
            "email": "sam.smoke@example.com",
            "phone": "+15550000001",
        },
    )
    assert cr.status_code == 201
    body = cr.json()
    assert body["email"] == "sam.smoke@example.com"
    assert "id" in body


def test_housekeeping_list_and_patch(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    prop_id = str(smoke_scenario["property_id"])
    room_id = smoke_scenario["room_id"]
    h = auth_headers_user(tid, oid, role="owner")
    lr = client.get(
        "/housekeeping",
        headers=h,
        params={"property_id": prop_id},
    )
    assert lr.status_code == 200
    rooms = lr.json()
    assert len(rooms) >= 1
    assert any(str(r["id"]) == str(room_id) for r in rooms)
    pr = client.patch(
        f"/housekeeping/{room_id}",
        headers=h,
        json={"housekeeping_status": "clean"},
    )
    assert pr.status_code == 200
    assert pr.json()["housekeeping_status"] == "clean"


def test_properties_patch(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    pid = smoke_scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.patch(
        f"/properties/{pid}",
        headers=h,
        json={"name": "Updated Smoke Property"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Smoke Property"


def test_api_key_create_returns_plaintext_once(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/api-keys",
        headers=h,
        json={
            "name": "integration-test",
            "scopes": ["guests:read"],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "integration-test"
    assert "key" in data
    assert len(data["key"]) > 20


def test_webhook_subscription_create(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/webhooks/subscriptions",
        headers=h,
        json={
            "url": "https://hooks.example.com/openpms",
            "events": ["booking.created"],
            "is_active": True,
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["url"] == "https://hooks.example.com/openpms"
    assert "secret" in data
    assert "booking.created" in data["events"]


def test_rate_plans_and_rates_list(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    pid = smoke_scenario["property_id"]
    rt_id = smoke_scenario["room_type_id"]
    rp_id = smoke_scenario["rate_plan_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r1 = client.get("/rate-plans", headers=h, params={"property_id": str(pid)})
    assert r1.status_code == 200
    assert any(str(x["id"]) == str(rp_id) for x in r1.json())
    r2 = client.get(
        "/rates",
        headers=h,
        params={
            "room_type_id": str(rt_id),
            "rate_plan_id": str(rp_id),
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
        },
    )
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


def test_inventory_override_errors_without_ledger(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    rt_id = smoke_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.put(
        "/inventory/availability/overrides",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
            "blocked_rooms": 1,
        },
    )
    assert r.status_code == 422


def test_rooms_create_second_room(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    rt_id = smoke_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/rooms",
        headers=h,
        json={"room_type_id": str(rt_id), "name": "102", "status": "available"},
    )
    assert r.status_code == 201
    assert r.json()["name"] == "102"


def test_audit_log_lists_after_mutation(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    client.post(
        "/guests",
        headers=h,
        json={
            "first_name": "Audit",
            "last_name": "Trail",
            "email": "audit.trail@example.com",
            "phone": "+15550000002",
        },
    )
    ar = client.get("/audit-log", headers=h, params={"limit": 20})
    assert ar.status_code == 200
    rows = ar.json()
    assert any(e.get("action") == "guest.create" for e in rows)
