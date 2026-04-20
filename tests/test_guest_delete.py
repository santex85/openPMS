"""DELETE /guests/{id}: success, 404, 409 when bookings exist."""

from __future__ import annotations

from uuid import UUID


def test_delete_guest_success(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    cr = client.post(
        "/guests",
        headers=h,
        json={
            "first_name": "Del",
            "last_name": "Guest",
            "email": "del.guest.unique@example.com",
            "phone": "+15550000999",
        },
    )
    assert cr.status_code == 201
    gid = cr.json()["id"]
    dr = client.delete(f"/guests/{gid}", headers=h)
    assert dr.status_code == 204
    gr = client.get(f"/guests/{gid}", headers=h)
    assert gr.status_code == 404


def test_delete_guest_404_unknown_id(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    from uuid import uuid4

    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.delete(f"/guests/{uuid4()}", headers=h)
    assert r.status_code == 404


def test_delete_guest_409_when_bookings_exist(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    guest_id = str(tenant_isolation_booking_scenario["guest_id"])
    r = client.delete(
        f"/guests/{guest_id}",
        headers=auth_headers(tid, role="owner"),
    )
    assert r.status_code == 409
    assert "bookings" in r.json()["detail"].lower()
