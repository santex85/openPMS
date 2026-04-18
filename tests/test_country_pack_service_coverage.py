"""Coverage for app.services.country_pack_service."""

from __future__ import annotations

import os
from datetime import time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.schemas.country_pack import CountryPackCreate, CountryPackPatch, TaxRuleSchema
from app.services.country_pack_service import (
    CountryPackServiceError,
    create_country_pack,
    delete_country_pack,
    update_country_pack,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def _sample_tax() -> TaxRuleSchema:
    return TaxRuleSchema(
        code="VAT",
        name="VAT",
        rate=Decimal("0.07"),
        inclusive=False,
        applies_to=["room_charge"],
        compound_after=None,
    )


def _create_payload(code: str) -> CountryPackCreate:
    return CountryPackCreate(
        code=code,
        name="Custom Pack",
        currency_code="usd",
        currency_symbol="$",
        currency_symbol_position="before",
        currency_decimal_places=2,
        timezone="UTC",
        date_format="YYYY-MM-DD",
        locale="en-US",
        default_checkin_time=time(14, 0),
        default_checkout_time=time(11, 0),
        taxes=[_sample_tax()],
        payment_methods=["cash"],
        fiscal_year_start=None,
    )


@pytest.mark.asyncio
async def test_create_country_pack_happy(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from uuid import uuid4

    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    code = f"cp_{tenant_id.hex[:8]}"

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
                    name="CpCov",
                    billing_email="cp@example.com",
                    status="active",
                ),
            )
            await session.flush()
            read = await create_country_pack(session, tenant_id, _create_payload(code))
            assert read.code == code
            assert read.name == "Custom Pack"


@pytest.mark.asyncio
async def test_create_country_pack_duplicate_409(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from uuid import uuid4

    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    code = f"d_{tenant_id.hex[:8]}"

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
                    name="CpCov2",
                    billing_email="cp2@example.com",
                    status="active",
                ),
            )
            await session.flush()
            await create_country_pack(session, tenant_id, _create_payload(code))
            with pytest.raises(CountryPackServiceError) as ei:
                await create_country_pack(session, tenant_id, _create_payload(code))
            assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_update_country_pack_patch_fields(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from uuid import uuid4

    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    code = f"u_{tenant_id.hex[:8]}"

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
                    name="CpCov3",
                    billing_email="cp3@example.com",
                    status="active",
                ),
            )
            await session.flush()
            await create_country_pack(session, tenant_id, _create_payload(code))

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            patch = CountryPackPatch(
                name="  Patched  ",
                timezone="Europe/Berlin",
                date_format="DD.MM.YYYY",
                payment_methods=["card", "cash"],
            )
            out = await update_country_pack(session, tenant_id, code, patch)
    assert out.name == "Patched"
    assert out.timezone == "Europe/Berlin"
    assert out.date_format == "DD.MM.YYYY"
    assert out.payment_methods == ["card", "cash"]


@pytest.mark.asyncio
async def test_update_country_pack_builtin_403(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from uuid import uuid4

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
                    name="CpCov4",
                    billing_email="cp4@example.com",
                    status="active",
                ),
            )
            await session.flush()
            with pytest.raises(CountryPackServiceError) as ei:
                await update_country_pack(
                    session,
                    tenant_id,
                    "TH",
                    CountryPackPatch(name="nope"),
                )
            assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_country_pack_in_use_409(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from uuid import uuid4

    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    code = f"del_{tenant_id.hex[:8]}"

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
                    name="CpCov5",
                    billing_email="cp5@example.com",
                    status="active",
                ),
            )
            await session.flush()
            await create_country_pack(session, tenant_id, _create_payload(code))
            prop = Property(
                tenant_id=tenant_id,
                name="Attached",
                timezone="UTC",
                currency="USD",
                country_pack_code=code,
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(CountryPackServiceError) as ei:
                await delete_country_pack(session, tenant_id, code)
            assert ei.value.status_code == 409
