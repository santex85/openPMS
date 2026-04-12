"""HTTP coverage for auth routes backed by auth_service (me, password, invite, patch)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password, hash_refresh_token
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.models.core.tenant import Tenant
from app.services.auth_service import purge_stale_refresh_tokens

from tests.db_seed import disable_row_security_for_test_seed


def test_get_me(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.get("/auth/me", headers=auth_headers(tid, user_id=oid, role="owner"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(oid)
    assert body["email"] == "owner@smoke.example.com"


def test_change_password_success(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "current_password": "secret",
            "new_password": "newsecret8",
        },
    )
    assert r.status_code == 204, r.text

    login = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "owner@smoke.example.com",
            "password": "newsecret8",
        },
    )
    assert login.status_code == 200, login.text


def test_change_password_wrong_current(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/change-password",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "current_password": "wrong-password",
            "new_password": "otherpass8",
        },
    )
    assert r.status_code == 401


def test_invite_user_success(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": "invited.viewer@smoke.example.com",
            "full_name": "Invited Viewer",
            "role": "viewer",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["email"] == "invited.viewer@smoke.example.com"
    assert body["user"]["role"] == "viewer"
    assert "temporary_password" in body


def test_invite_user_duplicate_email(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": "dup.invite@smoke.example.com",
            "full_name": "First",
            "role": "viewer",
        },
    )
    r2 = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": "dup.invite@smoke.example.com",
            "full_name": "Second",
            "role": "receptionist",
        },
    )
    assert r2.status_code == 409


def test_invite_user_invalid_role(client, smoke_scenario: dict, auth_headers) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": "badrole@smoke.example.com",
            "full_name": "X",
            "role": "owner",
        },
    )
    assert r.status_code == 422


def test_patch_user_set_role_owner_changes_manager(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    mid = smoke_scenario["manager_id"]
    r = client.patch(
        f"/auth/users/{mid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"role": "viewer"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "viewer"


def test_patch_user_manager_cannot_modify_owner(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    mid = smoke_scenario["manager_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=mid, role="manager"),
        json={"role": "receptionist"},
    )
    assert r.status_code == 403


def test_patch_user_cannot_deactivate_self(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"is_active": False},
    )
    assert r.status_code == 400


def test_patch_user_last_owner_guard(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"role": "viewer"},
    )
    assert r.status_code == 409


def test_patch_user_empty_body_noop(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    mid = smoke_scenario["manager_id"]
    r = client.patch(
        f"/auth/users/{mid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={},
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "manager@smoke.example.com"


@pytest.mark.asyncio
async def test_purge_stale_refresh_tokens(db_engine: object) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="PurgeTenant",
                    billing_email="purge@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email="u@purge.example.com",
                    password_hash=hash_password("secret"),
                    full_name="U",
                    role="owner",
                ),
            )
            await session.flush()
            stale_id = uuid4()
            stale = RefreshToken(
                id=stale_id,
                tenant_id=tenant_id,
                user_id=user_id,
                token_hash=hash_refresh_token("stale-refresh-token-value"),
                expires_at=datetime.now(UTC) - timedelta(days=1),
                revoked_at=None,
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
            session.add(stale)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            deleted = await purge_stale_refresh_tokens(session)
            assert deleted >= 1

    async with factory() as session:
        row = await session.scalar(select(RefreshToken).where(RefreshToken.id == stale_id))
        assert row is None
