"""Targeted async tests for channex_service (validate_key, connect, status, activate, mappings)."""

from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache
from app.integrations.channex.schemas import (
    ChannexProperty,
    ChannexRatePlan,
    ChannexRoomType,
)
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.schemas.channex import RateMappingItem, RoomMappingItem
from app.services.channex_service import (
    ChannexServiceError,
    activate,
    connect,
    disconnect,
    get_channex_rates,
    get_channex_rooms,
    get_status,
    provision_channex_from_openpms,
    require_active_channex_link,
    save_rate_mappings,
    save_room_mappings,
    validate_key,
)

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url, _seed_channex_property


@pytest.mark.asyncio
async def test_validate_key_success() -> None:
    mock_client = MagicMock()
    mock_client.get_properties = AsyncMock(
        return_value=[ChannexProperty(id="cx-prop-1", title="Ext Hotel")],
    )
    with patch("app.services.channex_service.ChannexClient", return_value=mock_client):
        out = await validate_key(" my-key ", "production")
    assert len(out) == 1
    assert out[0].id == "cx-prop-1"
    mock_client.get_properties.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_happy_path(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    cx_prop = "channex-property-connect-1"
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
                    name="ConnectTenant",
                    billing_email="ct@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Connect Prop",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            property_id = prop.id

    with patch(
        "app.services.channex_service.validate_key",
        new_callable=AsyncMock,
        return_value=[ChannexProperty(id=cx_prop, title="Remote")],
    ):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tenant_id)},
                )
                row = await connect(
                    session,
                    tenant_id,
                    property_id,
                    "plain-api-key",
                    "sandbox",
                    cx_prop,
                )
                link_id = row.id

    async with factory() as session:
        stored = await session.scalar(
            select(ChannexPropertyLink).where(ChannexPropertyLink.id == link_id),
        )
    assert stored is not None
    assert stored.channex_property_id == cx_prop
    assert stored.channex_env == "sandbox"


@pytest.mark.asyncio
async def test_get_status_connected_with_maps(db_engine: object, channex_encrypt_env: None) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            st = await get_status(session, tid, pid)

    assert st.connected is True
    assert st.link is not None
    assert st.room_maps_count >= 1
    assert st.rate_maps_count >= 1
    assert len(st.room_type_maps) >= 1


@pytest.mark.asyncio
async def test_activate_creates_webhook_when_url_configured(
    db_engine: object,
    channex_encrypt_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="pending",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setenv("CHANNEX_WEBHOOK_URL", "https://example.com/webhooks/channex")
    clear_settings_cache()

    mock_client = AsyncMock()
    mock_client.create_webhook = AsyncMock(return_value={"data": {"id": "wh-created-1"}})

    try:
        with patch(
            "app.services.channex_service._client_for_link",
            return_value=mock_client,
        ):
            async with factory() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                        ),
                        {"tid": str(tid)},
                    )
                    link = await activate(session, tid, pid)
        assert link.status == "active"
        assert link.channex_webhook_id == "wh-created-1"
        mock_client.create_webhook.assert_awaited()
    finally:
        monkeypatch.delenv("CHANNEX_WEBHOOK_URL", raising=False)
        clear_settings_cache()


@pytest.mark.asyncio
async def test_require_active_channex_link_rejects_pending(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="pending",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(ChannexServiceError) as ei:
                await require_active_channex_link(session, tid, pid)
            assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_save_rate_mappings_happy_path(
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
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    rtm_id = ctx["room_type_map_id"]  # type: ignore[assignment]
    rp_id = ctx["rate_plan_id"]  # type: ignore[assignment]
    cx_rp = ctx["channex_rate_plan_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await save_rate_mappings(
                session,
                tid,
                pid,
                [
                    RateMappingItem(
                        room_type_map_id=rtm_id,
                        rate_plan_id=rp_id,
                        channex_rate_plan_id=str(cx_rp),
                        channex_rate_plan_name="Mapped rate",
                    ),
                ],
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            rows = (
                await session.scalars(
                    select(ChannexRatePlanMap).where(
                        ChannexRatePlanMap.tenant_id == tid,
                    ),
                )
            ).all()
    assert len(rows) == 1
    assert rows[0].channex_rate_plan_name == "Mapped rate"


@pytest.mark.asyncio
async def test_provision_channex_happy_path(
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
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    mock_client = MagicMock()
    mock_client.get_room_types = AsyncMock(return_value=[])
    mock_client.get_rate_plans = AsyncMock(return_value=[])
    mock_client.create_room_type = AsyncMock(
        return_value=ChannexRoomType(id="provisioned-rt-1", title="Deluxe"),
    )
    mock_client.create_rate_plan = AsyncMock(
        return_value=ChannexRatePlan(id="provisioned-rp-1", title="BAR / Deluxe"),
    )

    with patch(
        "app.services.channex_service._client_for_link",
        return_value=mock_client,
    ):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                out = await provision_channex_from_openpms(session, tid, pid)

    assert out.room_types_created >= 1
    assert out.rate_plans_created >= 1
    mock_client.create_room_type.assert_awaited()
    mock_client.create_rate_plan.assert_awaited()


@pytest.mark.asyncio
async def test_save_room_mappings_happy_path(
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
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    rt_id = ctx["room_type_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    new_cx = str(uuid4())
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await save_room_mappings(
                session,
                tid,
                pid,
                [
                    RoomMappingItem(
                        room_type_id=rt_id,
                        channex_room_type_id=new_cx,
                        channex_room_type_name="Remapped",
                    ),
                ],
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            maps = (
                await session.scalars(
                    select(ChannexRoomTypeMap).where(
                        ChannexRoomTypeMap.tenant_id == tid,
                    ),
                )
            ).all()
    assert len(maps) == 1
    assert maps[0].channex_room_type_id == new_cx
    assert maps[0].channex_room_type_name == "Remapped"


@pytest.mark.asyncio
async def test_save_rate_mappings_empty_clears(
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
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await save_rate_mappings(session, tid, pid, [])

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            n = await session.scalar(
                select(ChannexRatePlanMap.id).where(
                    ChannexRatePlanMap.tenant_id == tid,
                ).limit(1),
            )
    assert n is None


@pytest.mark.asyncio
async def test_activate_without_webhook_url(
    db_engine: object,
    channex_encrypt_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="pending",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setenv("CHANNEX_WEBHOOK_URL", "")
    clear_settings_cache()
    mock_client = AsyncMock()
    try:
        with patch(
            "app.services.channex_service._client_for_link",
            return_value=mock_client,
        ):
            async with factory() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                        ),
                        {"tid": str(tid)},
                    )
                    link = await activate(session, tid, pid)
        assert link.status == "active"
        mock_client.create_webhook.assert_not_called()
    finally:
        monkeypatch.delenv("CHANNEX_WEBHOOK_URL", raising=False)
        clear_settings_cache()


@pytest.mark.asyncio
async def test_disconnect_no_link_noop(db_engine: object) -> None:
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
                    name="DiscNoLink",
                    billing_email="dnl@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="No Channex",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            pid = prop.id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            await disconnect(session, tenant_id, pid)


@pytest.mark.asyncio
async def test_get_channex_rooms_and_rates(
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
    tid = ctx["tenant_id"]  # type: ignore[assignment]
    pid = ctx["property_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    mock_client = MagicMock()
    mock_client.get_room_types = AsyncMock(
        return_value=[
            ChannexRoomType(id="cx-a", title="A"),
            ChannexRoomType(id="cx-b", title="B"),
        ],
    )
    mock_client.get_rate_plans = AsyncMock(
        return_value=[ChannexRatePlan(id="rp-1", title="Rack")],
    )
    with patch(
        "app.services.channex_service._client_for_link",
        return_value=mock_client,
    ):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                rooms = await get_channex_rooms(session, tid, pid)
                rates = await get_channex_rates(session, tid, pid)
    assert len(rooms) == 2
    assert {r.id for r in rooms} == {"cx-a", "cx-b"}
    assert len(rates) == 1
    assert rates[0].id == "rp-1"
