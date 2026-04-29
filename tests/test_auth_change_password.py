"""POST /auth/change-password — hash update and session revocation."""

from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings


def test_change_password_success_returns_204(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"current_password": "secret", "new_password": "newsecret888"},
    )
    assert r.status_code == 204


def test_change_password_wrong_current_returns_401(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "current_password": "wrong-password",
            "new_password": "newsecret888",
        },
    )
    assert r.status_code == 401


def test_change_password_revokes_all_refresh_tokens(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    settings = get_settings()
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]

    lr = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "owner@smoke.example.com",
            "password": "secret",
        },
    )
    assert lr.status_code == 200, lr.text
    refresh_prev = client.cookies.get(settings.refresh_cookie_name)
    assert refresh_prev

    ch = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"current_password": "secret", "new_password": "another88888"},
    )
    assert ch.status_code == 204

    client.cookies.clear()
    rf = client.post(
        "/auth/refresh",
        json={"tenant_id": str(tid), "refresh_token": refresh_prev},
    )
    assert rf.status_code == 401


def test_change_password_new_password_works_for_login(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    new_pass = "brandnewpwd88888"
    r = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"current_password": "secret", "new_password": new_pass},
    )
    assert r.status_code == 204
    lg = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "owner@smoke.example.com",
            "password": new_pass,
        },
    )
    assert lg.status_code == 200, lg.text
