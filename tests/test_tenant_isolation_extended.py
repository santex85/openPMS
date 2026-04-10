"""Tenant isolation on additional read endpoints (RLS)."""

from __future__ import annotations

from uuid import UUID


def test_tenant_b_sees_no_properties(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_b = tenant_isolation_booking_scenario["tenant_b"]
    r = client.get("/properties", headers=auth_headers(tenant_b))
    assert r.status_code == 200
    assert r.json() == []


def test_tenant_b_guest_list_empty(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_b = tenant_isolation_booking_scenario["tenant_b"]
    r = client.get("/guests", headers=auth_headers(tenant_b))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_tenant_b_cannot_read_tenant_a_guest_detail(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    """Guest id exists for tenant A; tenant B receives 404 via RLS (empty lookup)."""
    tenant_b: UUID = tenant_isolation_booking_scenario["tenant_b"]
    guest_id = tenant_isolation_booking_scenario["guest_id"]
    r = client.get(f"/guests/{guest_id}", headers=auth_headers(tenant_b))
    assert r.status_code == 404
