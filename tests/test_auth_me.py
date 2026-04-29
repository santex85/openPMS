"""Integration tests for GET /auth/me."""

from __future__ import annotations

from uuid import uuid4


def test_get_me_returns_current_user(client) -> None:
    email = f"me-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "MeTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "Me",
        },
    )
    assert reg.status_code == 201
    data = reg.json()
    tok = data["access_token"]
    uid = data["user"]["id"]
    r = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == uid
    assert body["email"] == email
    assert body["role"] == "owner"


def test_get_me_unauthenticated_returns_4xx(client) -> None:
    r = client.get("/auth/me")
    assert r.status_code in (401, 403, 422)


def test_get_me_with_api_key_returns_403(
    client,
    properties_only_api_key: tuple[str, str],
) -> None:
    _tid, plain = properties_only_api_key
    r = client.get("/auth/me", headers={"X-API-Key": plain})
    assert r.status_code == 403
