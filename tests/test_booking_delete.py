"""DELETE /bookings/{id}: cascade, 404, 409 for checked-in/out."""

from __future__ import annotations

from uuid import UUID, uuid4


def test_delete_booking_success_confirmed_with_folio(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tenant_id, user_id=user_id, role="receptionist")
    dr = client.delete(f"/bookings/{booking_id}", headers=h)
    assert dr.status_code == 204
    gr = client.get(f"/bookings/{booking_id}", headers=h)
    assert gr.status_code == 404


def test_delete_booking_success_tenant_isolation_scenario(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    bid: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, role="owner")
    dr = client.delete(f"/bookings/{bid}", headers=h)
    assert dr.status_code == 204
    assert client.get(f"/bookings/{bid}", headers=h).status_code == 404


def test_delete_booking_404(
    client,
    smoke_scenario: dict,
    auth_headers_user,
) -> None:
    from uuid import UUID as UUIDType

    tid: UUIDType = smoke_scenario["tenant_id"]
    oid: UUIDType = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.delete(f"/bookings/{uuid4()}", headers=h)
    assert r.status_code == 404


def test_delete_booking_409_checked_in(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    user_id: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tenant_id, user_id=user_id, role="receptionist")
    r = client.delete(f"/bookings/{booking_id}", headers=h)
    assert r.status_code == 409
    assert "check-in" in r.json()["detail"].lower()
