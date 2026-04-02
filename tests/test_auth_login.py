"""POST /auth/login with optional tenant_id."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.tenant import Tenant


def test_login_without_tenant_id(client) -> None:
    """Single-tenant email: use a fresh user so dev DB is not ambiguous."""
    email = "login-without-tenant@example.com"
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
