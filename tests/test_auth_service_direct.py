"""Direct async tests for app.services.auth_service (real DB, no TestClient)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.tenant import Tenant
from app.schemas.auth import (
    AuthChangePasswordRequest,
    AuthInviteRequest,
    AuthLoginRequest,
    AuthRefreshRequest,
    AuthRegisterRequest,
    UserPatchRequest,
)
from app.services.auth_service import (
    AuthServiceError,
    change_password,
    invite_user,
    list_users,
    login,
    patch_user,
    refresh_session,
    register_tenant_owner,
)

from tests.db_seed import disable_row_security_for_test_seed


@pytest.mark.asyncio
async def test_register_tenant_owner_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            out = await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name=" Direct Org ",
                    email="owner-direct@example.com",
                    password="password123",
                    full_name="Owner Person",
                ),
                tenant_id=tenant_id,
            )
    assert out.tenant_id == tenant_id
    assert out.access_token
    assert out.refresh_token
    assert out.user.email == "owner-direct@example.com"
    assert out.user.role == "owner"


@pytest.mark.asyncio
async def test_login_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="Login Org",
                    email="login-direct@example.com",
                    password="password123",
                    full_name="U",
                ),
                tenant_id=tenant_id,
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            logged = await login(
                session,
                settings,
                AuthLoginRequest(
                    tenant_id=tenant_id,
                    email="login-direct@example.com",
                    password="password123",
                ),
            )
    assert logged.access_token
    assert logged.refresh_token


@pytest.mark.asyncio
async def test_login_inactive_user(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    uid = uuid4()
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
                    name="InactiveTen",
                    billing_email="in@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=uid,
                    tenant_id=tenant_id,
                    email="inactive@example.com",
                    password_hash=hash_password("password123"),
                    full_name="I",
                    role="viewer",
                    is_active=False,
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(AuthServiceError) as ei:
                await login(
                    session,
                    settings,
                    AuthLoginRequest(
                        tenant_id=tenant_id,
                        email="inactive@example.com",
                        password="password123",
                    ),
                )
            assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_password(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="WrongPwd Org",
                    email="wrongpwd@example.com",
                    password="password123",
                    full_name="U",
                ),
                tenant_id=tenant_id,
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(AuthServiceError) as ei:
                await login(
                    session,
                    settings,
                    AuthLoginRequest(
                        tenant_id=tenant_id,
                        email="wrongpwd@example.com",
                        password="not-the-password",
                    ),
                )
            assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_session_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    refresh_raw: str
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            reg = await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="Refresh Org",
                    email="refresh-direct@example.com",
                    password="password123",
                    full_name="U",
                ),
                tenant_id=tenant_id,
            )
            refresh_raw = reg.refresh_token

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            pair = await refresh_session(
                session,
                settings,
                AuthRefreshRequest(
                    tenant_id=tenant_id,
                    refresh_token=refresh_raw,
                ),
            )
    assert pair.access_token
    assert pair.refresh_token


@pytest.mark.asyncio
async def test_refresh_session_revoked_token(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            reg = await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="Revoke Org",
                    email="revoke@example.com",
                    password="password123",
                    full_name="U",
                ),
                tenant_id=tenant_id,
            )
            bad = reg.refresh_token + "x"

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(AuthServiceError) as ei:
                await refresh_session(
                    session,
                    settings,
                    AuthRefreshRequest(
                        tenant_id=tenant_id,
                        refresh_token=bad,
                    ),
                )
            assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_change_password_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    owner_id = uuid4()
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
                    name="ChgPwd",
                    billing_email="cp@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="chg@example.com",
                    password_hash=hash_password("oldpass12"),
                    full_name="O",
                    role="owner",
                    is_active=True,
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await change_password(
                session,
                tenant_id,
                owner_id,
                AuthChangePasswordRequest(
                    current_password="oldpass12",
                    new_password="newpass123",
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await login(
                session,
                settings,
                AuthLoginRequest(
                    tenant_id=tenant_id,
                    email="chg@example.com",
                    password="newpass123",
                ),
            )


@pytest.mark.asyncio
async def test_patch_user_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    owner_id = uuid4()
    mgr_id = uuid4()
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
                    name="PatchU",
                    billing_email="pu@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="patch-owner@example.com",
                    password_hash=hash_password("password123"),
                    full_name="Owner",
                    role="owner",
                    is_active=True,
                ),
            )
            session.add(
                User(
                    id=mgr_id,
                    tenant_id=tenant_id,
                    email="patch-mgr@example.com",
                    password_hash=hash_password("password123"),
                    full_name="Mgr",
                    role="receptionist",
                    is_active=True,
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            updated = await patch_user(
                session,
                tenant_id,
                actor_user_id=owner_id,
                actor_role="owner",
                target_user_id=mgr_id,
                body=UserPatchRequest(role="manager"),
            )
    assert updated.role == "manager"


@pytest.mark.asyncio
async def test_invite_user_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="Invite Org",
                    email="inv-owner@example.com",
                    password="password123",
                    full_name="O",
                ),
                tenant_id=tenant_id,
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            inv = await invite_user(
                session,
                tenant_id,
                AuthInviteRequest(
                    email="invited-worker@example.com",
                    full_name="Worker",
                    role="housekeeper",
                ),
            )
    assert inv.user.role == "housekeeper"
    assert inv.temporary_password


@pytest.mark.asyncio
async def test_list_users_direct(db_engine: object) -> None:
    settings = get_settings()
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await register_tenant_owner(
                session,
                settings,
                AuthRegisterRequest(
                    tenant_name="List Org",
                    email="list-owner@example.com",
                    password="password123",
                    full_name="O",
                ),
                tenant_id=tenant_id,
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            users = await list_users(session, tenant_id)
    assert len(users) >= 1
    assert any(u.email == "list-owner@example.com" for u in users)
