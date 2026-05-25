"""HTTP limits for stay length (MAX_STAY_NIGHTS)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from uuid import UUID

import pytest

from app.services.stay_dates import MAX_STAY_NIGHTS
from tests.booking_seed import database_url, seed_booking_post_environment


@pytest.fixture
def booking_max_patch_ctx() -> dict[str, UUID]:
    """Environment for PATCH extend: seeded rates for MAX_STAY nights + one extra ledger date."""
    if not database_url():
        pytest.skip("DATABASE_URL required")
    ci = date(2062, 3, 1)
    nights = [ci + timedelta(days=i) for i in range(MAX_STAY_NIGHTS + 1)]
    return asyncio.run(
        seed_booking_post_environment(nights=nights, total_rooms=3),
    )


def test_post_exactly_max_nights_returns_201(
    client,
    auth_headers,
) -> None:
    ci = date(2060, 6, 1)
    nights = [ci + timedelta(days=i) for i in range(MAX_STAY_NIGHTS)]
    co = ci + timedelta(days=MAX_STAY_NIGHTS)
    if not database_url():
        pytest.skip("DATABASE_URL required")
    ctx = asyncio.run(
        seed_booking_post_environment(nights=nights, total_rooms=5),
    )
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["property_id"]),
            "room_type_id": str(ctx["room_type_id"]),
            "rate_plan_id": str(ctx["rate_plan_id"]),
            "check_in": ci.isoformat(),
            "check_out": co.isoformat(),
            "guest": {
                "first_name": "Len",
                "last_name": "Stay",
                "email": "lenstay@fz.example.com",
                "phone": "+10009990001",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert r.status_code == 201


def test_post_one_night_over_max_returns_422(
    client,
    auth_headers,
) -> None:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    ctx = asyncio.run(seed_booking_post_environment())
    ci = date(2060, 12, 1)
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    co_bad = ci + timedelta(days=MAX_STAY_NIGHTS + 1)
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["property_id"]),
            "room_type_id": str(ctx["room_type_id"]),
            "rate_plan_id": str(ctx["rate_plan_id"]),
            "check_in": ci.isoformat(),
            "check_out": co_bad.isoformat(),
            "guest": {
                "first_name": "Too",
                "last_name": "Long",
                "email": "toolong@fz.example.com",
                "phone": "+10009990002",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert r.status_code == 422
    assert "stay cannot exceed" in str(r.json()).lower()


def test_post_equal_check_dates_returns_422(client, auth_headers) -> None:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    ctx = asyncio.run(seed_booking_post_environment())
    ci = date(2061, 1, 15)
    uid: UUID = ctx["user_id"]  # type: ignore[assignment]
    tid: UUID = ctx["tenant_id"]  # type: ignore[assignment]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["property_id"]),
            "room_type_id": str(ctx["room_type_id"]),
            "rate_plan_id": str(ctx["rate_plan_id"]),
            "check_in": ci.isoformat(),
            "check_out": ci.isoformat(),
            "guest": {
                "first_name": "Zero",
                "last_name": "Night",
                "email": "zeronight@fz.example.com",
                "phone": "+10009990003",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert r.status_code == 422
    assert "check_out must be after check_in".lower() in str(r.json()).lower()


def test_patch_extend_one_night_over_max_returns_422(
    client,
    booking_max_patch_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    ctx = booking_max_patch_ctx
    ci = date(2062, 3, 1)
    co_initial = ci + timedelta(days=MAX_STAY_NIGHTS)
    uid = ctx["user_id"]
    tid = ctx["tenant_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pr = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["property_id"]),
            "room_type_id": str(ctx["room_type_id"]),
            "rate_plan_id": str(ctx["rate_plan_id"]),
            "check_in": ci.isoformat(),
            "check_out": co_initial.isoformat(),
            "guest": {
                "first_name": "Patch",
                "last_name": "Extend",
                "email": "patchext@fz.example.com",
                "phone": "+10009990005",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert pr.status_code == 201
    bid = pr.json()["booking_id"]
    co_bad = co_initial + timedelta(days=1)
    r = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_out": co_bad.isoformat()},
    )
    assert r.status_code == 422
    assert str(MAX_STAY_NIGHTS) in str(r.json()["detail"])


def test_patch_shorten_within_max_returns_204(
    client,
    booking_max_patch_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    ctx = booking_max_patch_ctx
    ci = date(2062, 3, 1)
    co_initial = ci + timedelta(days=MAX_STAY_NIGHTS)
    uid = ctx["user_id"]
    tid = ctx["tenant_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pr = client.post(
        "/bookings",
        headers=h,
        json={
            "property_id": str(ctx["property_id"]),
            "room_type_id": str(ctx["room_type_id"]),
            "rate_plan_id": str(ctx["rate_plan_id"]),
            "check_in": ci.isoformat(),
            "check_out": co_initial.isoformat(),
            "guest": {
                "first_name": "Patch",
                "last_name": "ShrinkMax",
                "email": "patchshrinkmax@fz.example.com",
                "phone": "+10009990007",
            },
            "status": "confirmed",
            "force_new_guest": True,
        },
    )
    assert pr.status_code == 201
    bid = pr.json()["booking_id"]
    r = client.patch(
        f"/bookings/{bid}",
        headers=h,
        json={"check_out": (co_initial - timedelta(days=1)).isoformat()},
    )
    assert r.status_code == 204
