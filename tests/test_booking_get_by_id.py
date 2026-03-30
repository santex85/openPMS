"""GET /bookings/{id} returns BookingTapeRead outside list date window."""

from __future__ import annotations

from uuid import UUID


def test_get_booking_by_id_returns_tape(
    client, folio_scenario: dict, auth_headers
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    r = client.get(
        f"/bookings/{booking_id}",
        headers=auth_headers(tenant_id),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(booking_id)
    assert "guest" in data
    assert data["guest"]["first_name"] == "F"
    assert data["check_in_date"] is not None
    assert data["check_out_date"] is not None


def test_get_booking_by_id_404_other_tenant(
    client, tenant_isolation_booking_scenario, auth_headers
) -> None:
    bid_a: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    tid_b: UUID = tenant_isolation_booking_scenario["tenant_b"]  # type: ignore[assignment]
    r = client.get(
        f"/bookings/{bid_a}",
        headers=auth_headers(tid_b),
    )
    assert r.status_code == 404
