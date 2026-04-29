"""POST /auth/logout — revocation and clearing the refresh cookie."""

from __future__ import annotations

import pytest
from uuid import UUID, uuid4

from app.core.config import get_settings


def test_logout_from_body_returns_204(client) -> None:
    settings = get_settings()
    email = f"logout-body-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoBody",
            "email": email,
            "password": "secret12345",
            "full_name": "L",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    client.cookies.clear()
    r = client.post(
        "/auth/logout",
        json={"tenant_id": tid, "refresh_token": raw},
    )
    assert r.status_code == 204


def test_logout_from_cookie_returns_204(client) -> None:
    settings = get_settings()
    email = f"logout-ck-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoCk",
            "email": email,
            "password": "secret12345",
            "full_name": "L",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    assert client.cookies.get(settings.refresh_cookie_name)
    r = client.post("/auth/logout", json={"tenant_id": tid})
    assert r.status_code == 204


def test_logout_clears_refresh_cookie(client) -> None:
    settings = get_settings()
    email = f"logout-clear-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoClear",
            "email": email,
            "password": "secret12345",
            "full_name": "L",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    assert client.cookies.get(settings.refresh_cookie_name)
    r = client.post("/auth/logout", json={"tenant_id": tid})
    assert r.status_code == 204
    sc = r.headers.get("set-cookie") or ""
    assert settings.refresh_cookie_name in sc.lower()
    assert "max-age=0" in sc.lower()


def test_logout_invalidates_token(client) -> None:
    settings = get_settings()
    email = f"logout-inv-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoInv",
            "email": email,
            "password": "secret12345",
            "full_name": "L",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    client.post(
        "/auth/logout",
        json={"tenant_id": tid, "refresh_token": raw},
    )
    rr = client.post(
        "/auth/refresh",
        json={"tenant_id": tid, "refresh_token": raw},
    )
    assert rr.status_code == 401


def test_logout_missing_refresh_token_tenant_id_in_body_returns_204(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    """No refresh token in body or cookie; tenant_id may still be sent for RLS scope (no rows to revoke)."""
    tid = smoke_scenario["tenant_id"]
    r = client.post("/auth/logout", json={"tenant_id": str(tid)})
    assert r.status_code == 204


@pytest.mark.parametrize(
    "post_kwargs",
    [
        pytest.param({"json": {}}, id="json_empty_object"),
        pytest.param(
            {"content": b"", "headers": {"Content-Type": "application/json"}},
            id="empty_body_with_application_json",
        ),
    ],
)
def test_logout_optional_body_accepts_empty_json_body(
    client, post_kwargs: dict[str, object]
) -> None:
    """Optional Body() with default None should accept empty JSON, not 422."""
    r = client.post("/auth/logout", **post_kwargs)
    assert r.status_code == 204, r.text


def test_logout_post_without_body_or_content_type_returns_204(client) -> None:
    """POST with no body and no Content-Type (optional Body -> None)."""
    r = client.post("/auth/logout")
    assert r.status_code == 204, r.text


def test_logout_already_revoked_returns_204(client) -> None:
    settings = get_settings()
    email = f"logout-rev-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoRev",
            "email": email,
            "password": "secret12345",
            "full_name": "L",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    body = {"tenant_id": tid, "refresh_token": raw}
    r1 = client.post("/auth/logout", json=body)
    assert r1.status_code == 204
    r2 = client.post("/auth/logout", json=body)
    assert r2.status_code == 204
