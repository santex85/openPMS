"""GET /bookings/{id}: guest tape fields and tenant isolation."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from tests.booking_seed import database_url, seed_booking_post_environment


@pytest.fixture
def merged_email_ctx() -> dict[str, object]:
    """Two-night span with optional guest row for merge tests."""
    if not database_url():
        pytest.skip("DATABASE_URL required")
    email = "merge@guest-read.example.com"
    base = asyncio.run(
        seed_booking_post_environment(existing_guest_email=email),
    )
    return {
        **base,
        "guest_email": email,
    }


def test_two_bookings_same_email_merge_share_guest_id(
    client,
    merged_email_ctx: dict[str, object],
    auth_headers,
) -> None:
    pid = merged_email_ctx["property_id"]
    rt = merged_email_ctx["room_type_id"]
    rp = merged_email_ctx["rate_plan_id"]
    uid: UUID = merged_email_ctx["user_id"]  # type: ignore[assignment]
    tid: UUID = merged_email_ctx["tenant_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")

    body_a = {
        "property_id": str(pid),
        "room_type_id": str(rt),
        "rate_plan_id": str(rp),
        "check_in": "2026-08-01",
        "check_out": "2026-08-03",
        "guest": {
            "first_name": "One",
            "last_name": "Merged",
            "email": str(merged_email_ctx["guest_email"]),
            "phone": "+7770000101",
        },
        "force_new_guest": False,
        "status": "confirmed",
    }
    ra = client.post("/bookings", headers=h, json=body_a)
    assert ra.status_code == 201
    gid_a = UUID(ra.json()["guest_id"])
    assert ra.json().get("guest_merged") is True

    body_b = {
        "property_id": str(pid),
        "room_type_id": str(rt),
        "rate_plan_id": str(rp),
        "check_in": "2026-09-01",
        "check_out": "2026-09-03",
        "guest": {
            "first_name": "Second",
            "last_name": "Stay",
            "email": str(merged_email_ctx["guest_email"]),
            "phone": "+7770000303",
        },
        "force_new_guest": False,
        "status": "confirmed",
    }
    rb = client.post("/bookings", headers=h, json=body_b)
    assert rb.status_code == 201
    assert UUID(rb.json()["guest_id"]) == gid_a


def test_same_email_two_tenants_are_distinct_guests(
    client,
    auth_headers,
) -> None:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    email = "shared@two-tenants.example.com"
    c1 = asyncio.run(seed_booking_post_environment())
    c2 = asyncio.run(seed_booking_post_environment())

    h1 = auth_headers(c1["tenant_id"], user_id=c1["user_id"], role="receptionist")
    h2 = auth_headers(c2["tenant_id"], user_id=c2["user_id"], role="receptionist")
    body = {
        "property_id": str(c1["property_id"]),
        "room_type_id": str(c1["room_type_id"]),
        "rate_plan_id": str(c1["rate_plan_id"]),
        "check_in": "2026-08-01",
        "check_out": "2026-08-03",
        "guest": {
            "first_name": "S",
            "last_name": "T",
            "email": email,
            "phone": "+7000000202",
        },
        "force_new_guest": False,
        "status": "confirmed",
    }
    r1 = client.post("/bookings", headers=h1, json=body)
    r2 = client.post(
        "/bookings",
        headers=h2,
        json={
            **body,
            "property_id": str(c2["property_id"]),
            "room_type_id": str(c2["room_type_id"]),
            "rate_plan_id": str(c2["rate_plan_id"]),
        },
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["guest_id"] != r2.json()["guest_id"]


def test_get_booking_returns_guest_names(
    client, folio_scenario: dict, auth_headers
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    uid: UUID = folio_scenario["user_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.get(f"/bookings/{bid}", headers=h)
    assert r.status_code == 200
    payload = r.json()
    assert payload["guest"]["first_name"]
    assert payload["guest"]["last_name"]
