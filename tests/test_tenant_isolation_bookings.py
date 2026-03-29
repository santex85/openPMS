"""Task 5.1: tenant A bookings must not be visible to tenant B (RLS)."""

from __future__ import annotations


def test_tenant_b_lists_empty_bookings_when_only_tenant_a_has_bookings(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_b = tenant_isolation_booking_scenario["tenant_b"]
    property_id = tenant_isolation_booking_scenario["property_id"]
    response = client.get(
        "/bookings",
        params={
            "property_id": str(property_id),
            "start_date": "2026-03-01",
            "end_date": "2026-03-31",
        },
        headers=auth_headers(tenant_b),
    )
    assert response.status_code == 200
    assert response.json() == []


def test_tenant_a_sees_own_booking(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_a = tenant_isolation_booking_scenario["tenant_a"]
    booking_id = str(tenant_isolation_booking_scenario["booking_id"])
    property_id = tenant_isolation_booking_scenario["property_id"]
    response = client.get(
        "/bookings",
        params={
            "property_id": str(property_id),
            "start_date": "2026-03-01",
            "end_date": "2026-03-31",
        },
        headers=auth_headers(tenant_a),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == booking_id
    assert data[0]["tenant_id"] == str(tenant_a)
