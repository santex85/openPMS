"""POST /auth/login with optional tenant_id."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache, get_settings
from app.core.jwt_keys import decode_access_token
from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.tenant import Tenant

from tests.db_seed import disable_row_security_for_test_seed


def test_login_without_tenant_id(client) -> None:
    """Single-tenant email: unique per run so reused dev DB is not ambiguous."""
    email = f"login-without-tenant-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoginWithoutTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "Login Without Tenant",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]

    r = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "secret12345",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["email"] == email
    assert data["user"]["tenant_id"] == tid
    assert "access_token" in data


def test_login_without_tenant_id_unknown_email(client) -> None:
    r = client.post(
        "/auth/login",
        json={
            "email": "nobody@smoke.example.com",
            "password": "secret",
        },
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверные данные."


def test_login_with_explicit_tenant_id(
    client,
    smoke_scenario: dict[str, UUID],
) -> None:
    tid = smoke_scenario["tenant_id"]
    r = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "owner@smoke.example.com",
            "password": "secret",
        },
    )
    assert r.status_code == 200
    assert r.json()["user"]["tenant_id"] == str(tid)


def test_login_without_tenant_id_ambiguous_email(
    client,
    db_engine: AsyncEngine,
) -> None:
    duplicate = "ambiguous-login-dup@example.com"
    t1, t2 = uuid4(), uuid4()
    u1, u2 = uuid4(), uuid4()

    async def _seed() -> None:
        factory = async_sessionmaker(
            db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(t1)},
                )
                session.add(
                    Tenant(
                        id=t1,
                        name="AmbOne",
                        billing_email="a1@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=u1,
                        tenant_id=t1,
                        email=duplicate,
                        password_hash=hash_password("secret"),
                        full_name="User One",
                        role="owner",
                        is_active=True,
                    ),
                )
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(t2)},
                )
                session.add(
                    Tenant(
                        id=t2,
                        name="AmbTwo",
                        billing_email="a2@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=u2,
                        tenant_id=t2,
                        email=duplicate,
                        password_hash=hash_password("secret"),
                        full_name="User Two",
                        role="owner",
                        is_active=True,
                    ),
                )

    asyncio.run(_seed())

    r = client.post(
        "/auth/login",
        json={"email": duplicate, "password": "secret"},
    )
    assert r.status_code == 401
    assert "Несколько аккаунтов" in r.json()["detail"]

    ok = client.post(
        "/auth/login",
        json={
            "tenant_id": str(t2),
            "email": duplicate,
            "password": "secret",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["tenant_id"] == str(t2)


def test_refresh_with_http_only_cookie_rotates_and_old_token_rejected(
    client,
) -> None:
    settings = get_settings()
    cookie_name = settings.refresh_cookie_name
    email = f"refresh-cookie-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "RefreshCookieTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "Refresh Cookie",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    old_refresh = client.cookies.get(cookie_name)
    assert old_refresh is not None

    r = client.post("/auth/refresh", json={"tenant_id": tid})
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert r.json()["token_type"] == "bearer"
    new_refresh = client.cookies.get(cookie_name)
    assert new_refresh is not None
    assert new_refresh != old_refresh

    client.cookies.set(cookie_name, old_refresh)
    stale = client.post("/auth/refresh", json={"tenant_id": tid})
    assert stale.status_code == 401


def test_access_token_exp_matches_settings_ttl(client) -> None:
    settings = get_settings()
    email = f"access-ttl-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "TtlTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "TTL User",
        },
    )
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    payload = decode_access_token(settings, token)
    exp = int(payload["exp"])
    iat = int(payload["iat"])
    delta_sec = exp - iat
    assert delta_sec == settings.access_token_ttl_minutes * 60


def test_register_forbidden_when_public_registration_disabled(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "false")
    clear_settings_cache()
    try:
        r = client.post(
            "/auth/register",
            json={
                "tenant_name": "NoPublicReg",
                "email": f"nopub-{uuid4()}@example.com",
                "password": "secret12345",
                "full_name": "No Pub",
            },
        )
        assert r.status_code == 403
        assert "invite" in r.json()["detail"].lower()
    finally:
        monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "true")
        clear_settings_cache()
