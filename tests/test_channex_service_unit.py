"""Unit tests for channex_service edge cases (mappings, disconnect)."""

from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.channex.client import ChannexApiError
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.schemas.channex import RoomMappingItem
from app.services.channex_service import (
    ChannexServiceError,
    disconnect,
    provision_channex_from_openpms,
    save_room_mappings,
)

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url, _seed_channex_property


@pytest.mark.asyncio
async def test_provision_raises_when_channex_not_connected(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                Tenant(
                    id=tid,
                    name="NoChannexTenant",
                    billing_email="nc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tid,
                name="NoChannex Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            pid = prop.id

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(ChannexServiceError) as ei:
                await provision_channex_from_openpms(session, tid, pid)
            assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_save_room_mappings_rejects_foreign_room_type(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    pid = ctx["property_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    bogus_rt = uuid4()
    async with factory() as session:
        async with session.begin():
            with pytest.raises(ChannexServiceError) as ei:
                await save_room_mappings(
                    session,
                    tid,
                    pid,
                    [
                        RoomMappingItem(
                            room_type_id=bogus_rt,
                            channex_room_type_id=str(uuid4()),
                        ),
                    ],
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_disconnect_swallows_delete_webhook_api_error(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=str(uuid4()),
    )
    tid = ctx["tenant_id"]
    pid = ctx["property_id"]
    link_id = ctx["link_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    mock_client = AsyncMock()
    mock_client.delete_webhook = AsyncMock(
        side_effect=ChannexApiError("gone", status_code=410),
    )
    with patch(
        "app.services.channex_service._client_for_link",
        return_value=mock_client,
    ):
        async with factory() as session:
            async with session.begin():
                await disconnect(session, tid, pid)

    async with factory() as session:
        link = await session.scalar(
            select(ChannexPropertyLink).where(ChannexPropertyLink.id == link_id),
        )
    assert link is None
    mock_client.delete_webhook.assert_awaited()
