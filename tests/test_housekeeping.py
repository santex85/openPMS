"""Housekeeping status transitions and role-based access."""

from __future__ import annotations


def test_housekeeping_status_transitions_dirty_to_inspected(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    prop_id = str(smoke_scenario["property_id"])
    room_id = smoke_scenario["room_id"]
    h_hk = auth_headers_user(tid, oid, role="housekeeper")

    dirty = client.get(
        "/housekeeping",
        headers=h_hk,
        params={"property_id": prop_id},
    )
    assert dirty.status_code == 200
    row = next(r for r in dirty.json() if str(r["id"]) == str(room_id))
    assert row["housekeeping_status"] == "dirty"

    c1 = client.patch(
        f"/housekeeping/{room_id}",
        headers=h_hk,
        json={"housekeeping_status": "clean"},
    )
    assert c1.status_code == 200
    assert c1.json()["housekeeping_status"] == "clean"

    c2 = client.patch(
        f"/housekeeping/{room_id}",
        headers=h_hk,
        json={"housekeeping_status": "inspected"},
    )
    assert c2.status_code == 200
    assert c2.json()["housekeeping_status"] == "inspected"

    rr = client.get(
        "/rooms",
        headers=h_hk,
        params={"property_id": prop_id},
    )
    assert rr.status_code == 200
    room_row = next(r for r in rr.json() if str(r["id"]) == str(room_id))
    assert room_row["status"] == "available"


def test_housekeeping_invalid_status_422(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    room_id = smoke_scenario["room_id"]
    h = auth_headers_user(tid, oid, role="housekeeper")
    r = client.patch(
        f"/housekeeping/{room_id}",
        headers=h,
        json={"housekeeping_status": "muddy"},
    )
    assert r.status_code == 422


def test_housekeeping_receptionist_read_only(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    uid = smoke_scenario["owner_id"]
    prop_id = str(smoke_scenario["property_id"])
    room_id = smoke_scenario["room_id"]
    h = auth_headers_user(tid, uid, role="receptionist")

    lr = client.get(
        "/housekeeping",
        headers=h,
        params={"property_id": prop_id},
    )
    assert lr.status_code == 200

    pr = client.patch(
        f"/housekeeping/{room_id}",
        headers=h,
        json={"housekeeping_status": "clean"},
    )
    assert pr.status_code == 403


def test_housekeeping_viewer_cannot_access_board(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    uid = smoke_scenario["owner_id"]
    prop_id = str(smoke_scenario["property_id"])
    h = auth_headers_user(tid, uid, role="viewer")

    lr = client.get(
        "/housekeeping",
        headers=h,
        params={"property_id": prop_id},
    )
    assert lr.status_code == 403
