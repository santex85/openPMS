"""POST /bookings: force_new_guest edge cases."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from tests.booking_seed import database_url, seed_booking_post_environment


@pytest.fixture
def booking_post_ctx_merge_email() -> dict[str, object]:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    email = "merge-http@fz.example.com"
    base = asyncio.run(
        seed_booking_post_environment(existing_guest_email=email),
    )
    return {**base, "guest_email": email}


@pytest.fixture
def booking_post_ctx_blank() -> dict[str, UUID]:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(seed_booking_post_environment())


def _post_booking_body(
    ctx: dict,
    *,
    email: str,
    force_new_guest: bool,
) -> dict:
    return {
        "property_id": str(ctx["property_id"]),
        "room_type_id": str(ctx["room_type_id"]),
        "rate_plan_id": str(ctx["rate_plan_id"]),
        "check_in": "2026-08-01",
        "check_out": "2026-08-03",
        "guest": {
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "phone": "+10000009999",
        },
        "status": "confirmed",
        "source": "api",
        "force_new_guest": force_new_guest,
    }


def test_create_booking_force_new_guest_true_creates_fresh_guest(
    client,
    booking_post_ctx_blank: dict[str, UUID],
    auth_headers,
) -> None:
    tid: UUID = booking_post_ctx_blank["tenant_id"]
    uid: UUID = booking_post_ctx_blank["user_id"]
    email = f"fresh-{uuid4().hex[:8]}@fz.example.com"
    r = client.post(
        "/bookings",
        headers=auth_headers(tid, user_id=uid, role="receptionist"),
        json=_post_booking_body(
            booking_post_ctx_blank, email=email, force_new_guest=True
        ),
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["guest_merged"] is False


def test_create_booking_force_new_guest_false_merges_existing(
    client,
    booking_post_ctx_merge_email: dict,
    auth_headers,
) -> None:
    tid: UUID = booking_post_ctx_merge_email["tenant_id"]  # type: ignore[assignment]
    uid: UUID = booking_post_ctx_merge_email["user_id"]  # type: ignore[assignment]
    email: str = booking_post_ctx_merge_email["guest_email"]  # type: ignore[assignment]
    r = client.post(
        "/bookings",
        headers=auth_headers(tid, user_id=uid, role="receptionist"),
        json=_post_booking_body(
            booking_post_ctx_merge_email, email=email, force_new_guest=False
        ),
    )
    assert r.status_code == 201, r.text
    assert r.json()["guest_merged"] is True


def test_create_booking_force_new_guest_email_collision_returns_409(
    client,
    booking_post_ctx_merge_email: dict,
    auth_headers,
) -> None:
    tid: UUID = booking_post_ctx_merge_email["tenant_id"]  # type: ignore[assignment]
    uid: UUID = booking_post_ctx_merge_email["user_id"]  # type: ignore[assignment]
    email: str = booking_post_ctx_merge_email["guest_email"]  # type: ignore[assignment]
    r = client.post(
        "/bookings",
        headers=auth_headers(tid, user_id=uid, role="receptionist"),
        json=_post_booking_body(
            booking_post_ctx_merge_email, email=email, force_new_guest=True
        ),
    )
    assert r.status_code == 409
    assert "email" in (r.json().get("detail") or "").lower()


def test_create_booking_force_new_guest_false_creates_new_when_no_match(
    client,
    booking_post_ctx_blank: dict[str, UUID],
    auth_headers,
) -> None:
    tid = booking_post_ctx_blank["tenant_id"]
    uid = booking_post_ctx_blank["user_id"]
    email = f"newonly-{uuid4().hex[:8]}@fz.example.com"
    r = client.post(
        "/bookings",
        headers=auth_headers(tid, user_id=uid, role="receptionist"),
        json=_post_booking_body(
            booking_post_ctx_blank, email=email, force_new_guest=False
        ),
    )
    assert r.status_code == 201, r.text
    assert r.json()["guest_merged"] is False
