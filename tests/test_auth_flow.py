"""POST /auth/register, /auth/login, /auth/refresh — full flow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache, get_settings
from app.core.security import hash_password, hash_refresh_token
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.models.core.tenant import Tenant

from tests.db_seed import disable_row_security_for_test_seed


def test_register_returns_201_with_access_token(client) -> None:
    email = f"reg-{uuid4()}@example.com"
    r = client.post(
        "/auth/register",
        json={
            "tenant_name": "RegTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "Registered User",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "access_token" in data
    assert data["user"]["email"] == email
    assert "tenant_id" in data


def test_register_sets_refresh_cookie(client) -> None:
    cookie_name = get_settings().refresh_cookie_name
    email = f"regcookie-{uuid4()}@example.com"
    r = client.post(
        "/auth/register",
        json={
            "tenant_name": "CookieTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "Cookie User",
        },
    )
    assert r.status_code == 201, r.text
    sc = r.headers.get("set-cookie", "")
    assert cookie_name in sc.lower()
    assert "httponly" in sc.lower()


def test_register_disabled_returns_403(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "false")
    clear_settings_cache()
    try:
        r = client.post(
            "/auth/register",
            json={
                "tenant_name": "Forbidden",
                "email": f"forbidden-{uuid4()}@example.com",
                "password": "secret12345",
                "full_name": "Forbidden",
            },
        )
        assert r.status_code == 403
    finally:
        monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "true")
        clear_settings_cache()


def test_register_duplicate_email_same_tenant_returns_4xx(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    email = f"dup-flow-{uuid4()}@example.com"
    r1 = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": email,
            "full_name": "First",
            "role": "receptionist",
        },
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": email,
            "full_name": "Second",
            "role": "receptionist",
        },
    )
    assert r2.status_code == 409


def test_login_success_returns_access_token(client) -> None:
    email = f"login-ok-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoginOk",
            "email": email,
            "password": "secret12345",
            "full_name": "Login Ok",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    r = client.post(
        "/auth/login",
        json={
            "tenant_id": tid,
            "email": email,
            "password": "secret12345",
        },
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_login_wrong_password_returns_401(client) -> None:
    email = f"login-badpass-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "LoginBad",
            "email": email,
            "password": "secret12345",
            "full_name": "X",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    r = client.post(
        "/auth/login",
        json={
            "tenant_id": tid,
            "email": email,
            "password": "wrong-password-8",
        },
    )
    assert r.status_code == 401


def test_login_inactive_user_returns_401(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    email = f"inactive-{uuid4()}@example.com"
    inv = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": email,
            "full_name": "Soon Inactive",
            "role": "receptionist",
        },
    )
    assert inv.status_code == 201, inv.text
    uid = inv.json()["user"]["id"]
    temp = inv.json()["temporary_password"]
    off = client.patch(
        f"/auth/users/{uid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"is_active": False},
    )
    assert off.status_code == 200, off.text
    r = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": email,
            "password": temp,
        },
    )
    assert r.status_code == 401


def test_login_unknown_email_returns_401(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    tid = smoke_scenario["tenant_id"]
    r = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "ghost-not-exists@example.com",
            "password": "secret12345",
        },
    )
    assert r.status_code == 401


def test_login_without_tenant_id_ambiguous_email_returns_401(
    client,
    db_engine: AsyncEngine,
) -> None:
    duplicate = f"ambiguous-flow-{uuid4()}@example.com"
    t1, t2 = uuid4(), uuid4()
    u1, u2 = uuid4(), uuid4()

    async def _seed() -> None:
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
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


def test_refresh_from_body_returns_new_access_token(client) -> None:
    settings = get_settings()
    email = f"refresh-body-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "RefreshBody",
            "email": email,
            "password": "secret12345",
            "full_name": "R",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    tok = client.cookies.get(settings.refresh_cookie_name)
    assert tok
    client.cookies.clear()
    r = client.post(
        "/auth/refresh",
        json={"tenant_id": tid, "refresh_token": tok},
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_refresh_from_cookie_returns_new_access_token(client) -> None:
    settings = get_settings()
    email = f"refresh-cookie-flow-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "RefreshCookieTenant",
            "email": email,
            "password": "secret12345",
            "full_name": "R",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    assert client.cookies.get(settings.refresh_cookie_name)
    r = client.post("/auth/refresh", json={"tenant_id": tid})
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert client.cookies.get(settings.refresh_cookie_name)


def test_refresh_rotated_old_token_rejected(client) -> None:
    settings = get_settings()
    email = f"rotate-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "Rotate",
            "email": email,
            "password": "secret12345",
            "full_name": "R",
        },
    )
    assert reg.status_code == 201
    tid = reg.json()["user"]["tenant_id"]
    old = client.cookies.get(settings.refresh_cookie_name)
    assert old
    r = client.post("/auth/refresh", json={"tenant_id": tid})
    assert r.status_code == 200
    bad = client.post(
        "/auth/refresh",
        json={
            "tenant_id": tid,
            "refresh_token": old,
        },
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_refresh_expired_token_returns_401(client, db_engine) -> None:
    settings = get_settings()
    email = f"exp-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "Expiry",
            "email": email,
            "password": "secret12345",
            "full_name": "E",
        },
    )
    assert reg.status_code == 201
    tid = UUID(reg.json()["user"]["tenant_id"])
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    digest = hash_refresh_token(raw)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.tenant_id == tid,
                    RefreshToken.token_hash == digest,
                )
                .values(expires_at=datetime.now(UTC) - timedelta(days=1))
            )

    client.cookies.clear()
    r = client.post(
        "/auth/refresh",
        json={"tenant_id": str(tid), "refresh_token": raw},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_revoked_token_returns_401(client, db_engine) -> None:
    settings = get_settings()
    email = f"revoked-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "RevokedTok",
            "email": email,
            "password": "secret12345",
            "full_name": "E",
        },
    )
    assert reg.status_code == 201
    tid = UUID(reg.json()["user"]["tenant_id"])
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    digest = hash_refresh_token(raw)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.tenant_id == tid,
                    RefreshToken.token_hash == digest,
                )
                .values(revoked_at=now)
            )

    client.cookies.clear()
    r = client.post(
        "/auth/refresh",
        json={"tenant_id": str(tid), "refresh_token": raw},
    )
    assert r.status_code == 401


def test_refresh_wrong_tenant_id_returns_401(client) -> None:
    settings = get_settings()
    email = f"wtenant-{uuid4()}@example.com"
    reg = client.post(
        "/auth/register",
        json={
            "tenant_name": "WrongT",
            "email": email,
            "password": "secret12345",
            "full_name": "W",
        },
    )
    assert reg.status_code == 201
    raw = client.cookies.get(settings.refresh_cookie_name)
    assert raw
    other = uuid4()
    client.cookies.clear()
    r = client.post(
        "/auth/refresh",
        json={"tenant_id": str(other), "refresh_token": raw},
    )
    assert r.status_code == 401


def test_refresh_missing_token_returns_401(client) -> None:
    tid = uuid4()
    client.cookies.clear()
    r = client.post("/auth/refresh", json={"tenant_id": str(tid)})
    assert r.status_code == 401


def test_refresh_failure_clears_cookie_after_invalid_attempt(client) -> None:
    settings = get_settings()
    fake_tid = uuid4()
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, "not-a-stored-token-value")
    r = client.post(
        "/auth/refresh",
        json={"tenant_id": str(fake_tid), "refresh_token": "bogus-refresh"},
    )
    assert r.status_code == 401
    set_cookie_parts = [
        hv
        for hk, hv in r.headers.multi_items()
        if hk.lower() == "set-cookie"
    ]
    merged = "; ".join(set_cookie_parts)
    assert settings.refresh_cookie_name.lower() in merged.lower()
