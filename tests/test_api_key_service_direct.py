"""Direct coverage for app.services.api_key_service."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core.tenant import Tenant
from app.services.api_key_service import (
    ApiKeyServiceError,
    create_api_key,
    delete_api_key,
    patch_api_key,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_create_api_key_empty_name_422(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
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
                    name="KeyCov",
                    billing_email="kc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            with pytest.raises(ApiKeyServiceError) as ei:
                await create_api_key(
                    session,
                    tenant_id,
                    name="   ",
                    scopes=["read"],
                    expires_at=None,
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_create_api_key_empty_scopes_422(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
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
                    name="KeyCov2",
                    billing_email="kc2@example.com",
                    status="active",
                ),
            )
            await session.flush()
            with pytest.raises(ApiKeyServiceError) as ei:
                await create_api_key(
                    session,
                    tenant_id,
                    name="n",
                    scopes=[],
                    expires_at=None,
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_patch_api_key_rename(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    key_id = None

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
                    name="KeyCov3",
                    billing_email="kc3@example.com",
                    status="active",
                ),
            )
            await session.flush()
            row, _plain = await create_api_key(
                session,
                tenant_id,
                name="Original",
                scopes=["read"],
                expires_at=None,
            )
            key_id = row.id

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            updated = await patch_api_key(
                session,
                tenant_id,
                key_id,
                name="Renamed",
                is_active=False,
            )
            assert updated.name == "Renamed"
            assert updated.is_active is False


@pytest.mark.asyncio
async def test_delete_api_key_not_found(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
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
                    name="KeyCov4",
                    billing_email="kc4@example.com",
                    status="active",
                ),
            )
            await session.flush()
            with pytest.raises(ApiKeyServiceError) as ei:
                await delete_api_key(session, tenant_id, uuid4())
            assert ei.value.status_code == 404
