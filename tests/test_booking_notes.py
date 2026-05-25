"""PATCH /bookings/{id} notes — schema max 8000; service truncation on accepted bodies."""

from __future__ import annotations

from uuid import UUID


def test_patch_notes_exactly_max_length_readable(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    txt = "a" * 8000
    r = client.patch(f"/bookings/{bid}", headers=h, json={"notes": txt})
    assert r.status_code == 204
    g = client.get(f"/bookings/{bid}", headers=h)
    assert g.status_code == 200
    assert g.json().get("notes") == txt


def test_patch_notes_over_schema_max_returns_422(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"notes": "z" * 9000},
    )
    assert r.status_code == 422


def test_patch_notes_null_clears(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    p1 = client.patch(f"/bookings/{bid}", headers=h, json={"notes": "temp note"})
    assert p1.status_code == 204
    p2 = client.patch(f"/bookings/{bid}", headers=h, json={"notes": None})
    assert p2.status_code == 204
    g = client.get(f"/bookings/{bid}", headers=h)
    assert g.json().get("notes") in (None, "")


def test_notes_empty_string_normalized_to_cleared(
    client,
    folio_scenario_confirmed: dict,
    auth_headers,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario_confirmed["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.patch(f"/bookings/{bid}", headers=h, json={"notes": "   \t  "})
    assert r.status_code == 204
    g = client.get(f"/bookings/{bid}", headers=h)
    assert g.json().get("notes") in (None, "")
