"""Coverage for app.services.extension_service."""

from __future__ import annotations

import os
from datetime import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.models.bookings.guest import Guest
from app.schemas.country_pack import ExtensionCreate
from app.services.extension_service import (
    ExtensionServiceError,
    list_extensions,
    register_extension,
    upsert_property_extension,
    validate_extension_required_fields_for_checkin,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_register_extension_happy(db_engine: object) -> None:
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
                    name="ExtCov",
                    billing_email="ex@example.com",
                    status="active",
                ),
            )
            await session.flush()
            data = ExtensionCreate(
                code="reg_ext",
                name="Registered",
                country_code="DE",
                webhook_url="https://example.com/ext-reg",
                required_fields=["passport_data"],
            )
            out = await register_extension(session, tenant_id, data)
    assert out.code == "reg_ext"
    assert out.country_code == "DE"


@pytest.mark.asyncio
async def test_register_extension_duplicate_409(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    data = ExtensionCreate(
        code="dup_ext",
        name="Dup",
        country_code=None,
        webhook_url="https://example.com/dup",
    )

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
                    name="ExtCov2",
                    billing_email="ex2@example.com",
                    status="active",
                ),
            )
            await session.flush()
            await register_extension(session, tenant_id, data)
            with pytest.raises(ExtensionServiceError) as ei:
                await register_extension(session, tenant_id, data)
            assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_list_extensions_with_country_code(db_engine: object) -> None:
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
                    name="ExtCov3",
                    billing_email="ex3@example.com",
                    status="active",
                ),
            )
            await session.flush()
            await register_extension(
                session,
                tenant_id,
                ExtensionCreate(
                    code="th_only",
                    name="A",
                    country_code="TH",
                    webhook_url="https://example.com/a",
                ),
            )
            await register_extension(
                session,
                tenant_id,
                ExtensionCreate(
                    code="global_ext",
                    name="B",
                    country_code=None,
                    webhook_url="https://example.com/b",
                ),
            )

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            rows = await list_extensions(session, tenant_id, country_code="th")
    codes = {r.code for r in rows}
    assert "th_only" in codes
    assert "global_ext" in codes


@pytest.mark.asyncio
async def test_upsert_property_extension_insert_and_update(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    pid: object = None
    eid: object = None

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
                    name="ExtCov4",
                    billing_email="ex4@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="P",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            ext = await register_extension(
                session,
                tenant_id,
                ExtensionCreate(
                    code="upsert_me",
                    name="U",
                    webhook_url="https://example.com/u",
                ),
            )
            pid = prop.id
            eid = ext.id

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            first = await upsert_property_extension(
                session,
                tenant_id,
                pid,  # type: ignore[arg-type]
                eid,  # type: ignore[arg-type]
                config={"a": 1},
                is_active=True,
            )
            assert first.is_active is True

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            second = await upsert_property_extension(
                session,
                tenant_id,
                pid,  # type: ignore[arg-type]
                eid,  # type: ignore[arg-type]
                config={"a": 2},
                is_active=False,
            )
            assert second.is_active is False
            assert second.config == {"a": 2}


@pytest.mark.asyncio
async def test_validate_extension_required_fields_empty(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    guest_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    prop_id: object = None

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
                    name="ExtCov5",
                    billing_email="ex5@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="NoExt",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            prop_id = prop.id
            session.add(
                Guest(
                    id=guest_id,
                    tenant_id=tenant_id,
                    first_name="G",
                    last_name="H",
                    email=f"g-{guest_id}@ex.example.com",
                    phone="+1",
                ),
            )
            await session.flush()

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            guest = await session.get(Guest, guest_id)
            assert guest is not None
            msgs = await validate_extension_required_fields_for_checkin(
                session,
                tenant_id,
                prop_id,  # type: ignore[arg-type]
                guest,
            )
    assert msgs == []
