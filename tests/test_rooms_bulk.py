"""POST /rooms/bulk — bulk physical room creation."""

from __future__ import annotations

from uuid import UUID


def test_rooms_bulk_create_five(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    rt_id = smoke_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/rooms/bulk",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "on_conflict": "fail",
            "rooms": [
                {"name": f"bulk-{i}", "status": "available"}
                for i in range(5)
            ],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert len(data["created"]) == 5
    assert data["skipped"] == []
    names = {row["name"] for row in data["created"]}
    assert names == {f"bulk-{i}" for i in range(5)}


def test_rooms_bulk_skip_existing(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    rt_id = smoke_scenario["room_type_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/rooms/bulk",
        headers=h,
        json={
            "room_type_id": str(rt_id),
            "on_conflict": "skip",
            "rooms": [
                {"name": "101", "status": "available"},
                {"name": "skip-new-1", "status": "available"},
            ],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert "101" in data["skipped"]
    assert len(data["created"]) == 1
    assert data["created"][0]["name"] == "skip-new-1"
