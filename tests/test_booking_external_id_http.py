"""external_booking_id: HTTP tape query, duplicates, duplicate POST, validation length."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from tests.booking_seed import database_url, seed_booking_post_environment


@pytest.fixture
def ext_ctx() -> dict[str, UUID]:
    if not database_url():
        pytest.skip("DATABASE_URL required")
    ctx = asyncio.run(seed_booking_post_environment())
    return ctx


def _post_body(
    ctx: dict[str, UUID],
    *,
    email: str,
    ext: str | None,
    ci: str,
    co: str,
) -> dict:
    out: dict = {
        "property_id": str(ctx["property_id"]),
        "room_type_id": str(ctx["room_type_id"]),
        "rate_plan_id": str(ctx["rate_plan_id"]),
        "check_in": ci,
        "check_out": co,
        "guest": {
            "first_name": "Ext",
            "last_name": "Http",
            "email": email,
            "phone": "+9000880011",
        },
        "status": "confirmed",
        "force_new_guest": True,
    }
    if ext is not None:
        out["external_booking_id"] = ext
    return out


def test_get_bookings_filters_by_external_id(
    client,
    ext_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    uid = ext_ctx["user_id"]
    tid = ext_ctx["tenant_id"]
    pid = ext_ctx["property_id"]
    ext = "ext-http-filter-zzz"
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pr = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="e1-ext@fz.example.com",
            ext=ext,
            ci="2026-11-01",
            co="2026-11-03",
        ),
    )
    assert pr.status_code == 201
    r = client.get(
        "/bookings",
        headers=h,
        params={
            "external_booking_id": ext,
            "limit": "10",
            "offset": "0",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    row = data["items"][0]
    assert row["property_id"] == str(pid)
    assert row["external_booking_id"] == ext


def test_get_booking_single_includes_external_id(
    client,
    ext_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    uid = ext_ctx["user_id"]
    tid = ext_ctx["tenant_id"]
    ext = "ext-single-get-detail"
    h = auth_headers(tid, user_id=uid, role="receptionist")
    pr = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="singleext@fz.example.com",
            ext=ext,
            ci="2026-12-01",
            co="2026-12-02",
        ),
    )
    assert pr.status_code == 201
    bid = pr.json()["booking_id"]
    r = client.get(f"/bookings/{bid}", headers=h)
    assert r.status_code == 200
    assert r.json().get("external_booking_id") == ext


def test_post_duplicate_external_id_conflict(
    client,
    ext_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    uid = ext_ctx["user_id"]
    tid = ext_ctx["tenant_id"]
    ext = "dup-http-conflict-flag"
    h = auth_headers(tid, user_id=uid, role="receptionist")
    p1 = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="dupa@fz.example.com",
            ext=ext,
            ci="2026-12-01",
            co="2026-12-02",
        ),
    )
    assert p1.status_code == 201
    p2 = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="dupb@fz.example.com",
            ext=ext,
            ci="2026-11-01",
            co="2026-11-03",
        ),
    )
    assert p2.status_code == 409


def test_external_booking_id_over_schema_max_length_returns_422(
    client,
    ext_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    uid = ext_ctx["user_id"]
    tid = ext_ctx["tenant_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    long_ext = "x" * 200
    r = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="too-long-ext@fz.example.com",
            ext=long_ext,
            ci="2026-09-01",
            co="2026-09-03",
        ),
    )
    assert r.status_code == 422


def test_optional_external_left_null_not_in_json(
    client,
    ext_ctx: dict[str, UUID],
    auth_headers,
) -> None:
    uid = ext_ctx["user_id"]
    tid = ext_ctx["tenant_id"]
    h = auth_headers(tid, user_id=uid, role="receptionist")
    r = client.post(
        "/bookings",
        headers=h,
        json=_post_body(
            ext_ctx,
            email="no-ext-id@fz.example.com",
            ext=None,
            ci="2026-08-01",
            co="2026-08-03",
        ),
    )
    assert r.status_code == 201
    bid = r.json()["booking_id"]
    g = client.get(f"/bookings/{bid}", headers=h)
    assert g.status_code == 200
    assert g.json().get("external_booking_id") in (None, "")
