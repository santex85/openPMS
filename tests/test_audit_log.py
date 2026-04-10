"""Audit log list: pagination and filters."""

from __future__ import annotations

from uuid import UUID

def test_audit_log_pagination(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    for i in range(3):
        r = client.post(
            "/guests",
            headers=h,
            json={
                "first_name": "Pag",
                "last_name": f"Guest{i}",
                "email": f"pag.guest{i}.audit@example.com",
                "phone": f"+1555000100{i}",
            },
        )
        assert r.status_code == 201

    all_rows = client.get("/audit-log", headers=h, params={"limit": 50}).json()
    guest_creates = [e for e in all_rows if e.get("action") == "guest.create"]
    assert len(guest_creates) >= 3

    page0 = client.get("/audit-log", headers=h, params={"limit": 1, "offset": 0}).json()
    page1 = client.get("/audit-log", headers=h, params={"limit": 1, "offset": 1}).json()
    assert len(page0) == 1
    assert len(page1) == 1
    assert page0[0]["id"] != page1[0]["id"]


def test_audit_log_filter_action_and_entity_type(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    pid = smoke_scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")

    client.patch(
        f"/properties/{pid}",
        headers=h,
        json={"name": "Audit Filter Property Name"},
    )

    by_action = client.get(
        "/audit-log",
        headers=h,
        params={"action": "property.patch", "limit": 20},
    )
    assert by_action.status_code == 200
    act_rows = by_action.json()
    assert all(e["action"] == "property.patch" for e in act_rows)
    assert any(e["entity_type"] == "property" for e in act_rows)

    by_entity = client.get(
        "/audit-log",
        headers=h,
        params={"entity_type": "property", "limit": 10},
    )
    assert by_entity.status_code == 200
    ent_rows = by_entity.json()
    assert all(e["entity_type"] == "property" for e in ent_rows)


def test_audit_log_manager_can_read(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    """Manager role can list audit log (requires JWT user in tenant with manager role)."""
    tid = smoke_scenario["tenant_id"]
    mid = smoke_scenario["manager_id"]
    h = auth_headers_user(tid, mid, role="manager")
    r = client.get("/audit-log", headers=h, params={"limit": 5})
    assert r.status_code == 200
