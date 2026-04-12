"""Point coverage for remaining app.services.channex_service helpers."""

from __future__ import annotations

import os
from datetime import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.channex.client import ChannexApiError
from app.integrations.channex.schemas import ChannexProperty
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.services.channex_service import (
    ChannexServiceError,
    _channex_http_to_service,
    _normalize_env,
    create_channex_property_from_openpms,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def test_normalize_env_invalid() -> None:
    with pytest.raises(ChannexServiceError) as ei:
        _normalize_env("bogus")
    assert ei.value.status_code == 422


def test_channex_http_to_service_401_and_422() -> None:
    e401 = ChannexApiError("unauth", status_code=401)
    out401 = _channex_http_to_service(e401)
    assert out401.status_code == 401

    e422 = ChannexApiError("bad", status_code=422, body="short body")
    out422 = _channex_http_to_service(e422)
    assert out422.status_code == 422
    assert out422.detail == "short body"


@pytest.mark.asyncio
async def test_create_channex_property_from_openpms(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    prop_id = None

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
                    name="ChxCov",
                    billing_email="cx@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Hotel X",
                timezone="Asia/Bangkok",
                currency="THB",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            prop_id = prop.id

    assert prop_id is not None

    fake = ChannexProperty(id="chnx-1", title="Hotel X")

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            with patch(
                "app.services.channex_service.ChannexClient",
            ) as mock_cls:
                inst = mock_cls.return_value
                inst.create_property = AsyncMock(return_value=fake)
                out = await create_channex_property_from_openpms(
                    session,
                    tenant_id,
                    prop_id,
                    api_key="k",
                    env="sandbox",
                )
                assert out.id == "chnx-1"
                inst.create_property.assert_awaited_once_with(
                    "Hotel X",
                    "THB",
                    "Asia/Bangkok",
                )
