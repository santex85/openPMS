"""GET /auth/users lists tenant users (owner/manager only)."""

from __future__ import annotations

from uuid import UUID


def test_list_users_sorted_by_email(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get("/auth/users", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    emails = [u["email"] for u in data]
    assert emails == sorted(emails)
    assert any(u["email"] == "owner@smoke.example.com" for u in data)


def test_list_users_receptionist_forbidden(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="receptionist")
    r = client.get("/auth/users", headers=h)
    assert r.status_code == 403
