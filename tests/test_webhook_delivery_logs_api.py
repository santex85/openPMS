"""GET /webhooks/delivery-logs."""

from __future__ import annotations

from uuid import UUID


def test_list_webhook_delivery_logs_empty_ok(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get("/webhooks/delivery-logs", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_list_webhook_delivery_logs_pagination_params(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get(
        "/webhooks/delivery-logs",
        headers=h,
        params={"limit": 10, "offset": 0},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
