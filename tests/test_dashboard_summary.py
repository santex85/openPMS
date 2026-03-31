"""GET /dashboard/summary."""

from __future__ import annotations

from uuid import UUID


def test_dashboard_summary_shape(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    pid = smoke_scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get(
        "/dashboard/summary",
        headers=h,
        params={"property_id": str(pid)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "USD"
    assert "arrivals_today" in body
    assert "departures_today" in body
    assert "occupied_rooms" in body
    assert "total_rooms" in body
    assert "dirty_rooms" in body
    assert body["dirty_rooms"] >= 1


def test_dashboard_summary_unknown_property_404(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get(
        "/dashboard/summary",
        headers=h,
        params={"property_id": str(UUID(int=0))},
    )
    assert r.status_code == 404
