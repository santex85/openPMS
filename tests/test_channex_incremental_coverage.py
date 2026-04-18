"""Extra incremental ARI coverage: stop_sell, skip branches, API errors."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import clear_settings_cache, get_settings
from app.core.security import hash_password
from app.integrations.channex.client import ChannexApiError
from app.integrations.channex.crypto import encrypt_channex_api_key
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.models.core.room_type import RoomType
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.tasks.channex_incremental_ari import (
    _run_push_channex_availability,
    _run_push_channex_rates,
    _run_push_channex_stop_sell,
)

from tests.db_seed import disable_row_security_for_test_seed


@pytest.fixture
def channex_encrypt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("WEBHOOK_SECRET_FERNET_KEY", key)
    clear_settings_cache()


@pytest.fixture
def incremental_ctx(channex_encrypt_env: None) -> dict[str, object]:
    url = _database_url()
    if not url:
        pytest.skip("Set DATABASE_URL for integration tests")

    tenant_id = uuid4()
    owner_id = uuid4()
    cx_property_id = str(uuid4())
    seed_engine = create_async_engine(url)
    factory = async_sessionmaker(
        seed_engine, class_=AsyncSession, expire_on_commit=False
    )
    settings = get_settings()
    enc_key = encrypt_channex_api_key(settings, "incremental-test-key")

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="IncrementalTenantCov",
                        billing_email="inccov@example.com",
                        status="active",
                    ),
                )
                await session.flush()
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="o@inccov.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Inc Hotel Cov",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                rt = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="Std",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(rt)
                await session.flush()
                rp = RatePlan(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rp)
                await session.flush()
                link = ChannexPropertyLink(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    channex_property_id=cx_property_id,
                    channex_api_key=enc_key,
                    channex_env="production",
                    status="active",
                )
                session.add(link)
                await session.flush()
                rtm = ChannexRoomTypeMap(
                    tenant_id=tenant_id,
                    property_link_id=link.id,
                    room_type_id=rt.id,
                    channex_room_type_id=str(uuid4()),
                )
                session.add(rtm)
                await session.flush()
                rpm = ChannexRatePlanMap(
                    tenant_id=tenant_id,
                    room_type_map_id=rtm.id,
                    rate_plan_id=rp.id,
                    channex_rate_plan_id=str(uuid4()),
                )
                session.add(rpm)
                today = datetime.now(UTC).date()
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=today,
                        total_rooms=5,
                        booked_rooms=1,
                        blocked_rooms=1,
                    ),
                )
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=today,
                        price=Decimal("199.50"),
                        stop_sell=False,
                        min_stay_arrival=2,
                        max_stay=14,
                    ),
                )
                return {
                    "tenant_id": tenant_id,
                    "property_id": prop.id,
                    "room_type_id": rt.id,
                    "rate_plan_id": rp.id,
                    "today": today,
                }

    try:
        return asyncio.run(_seed())
    finally:
        asyncio.run(seed_engine.dispose())


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_tenant_property_no_channex() -> dict[str, object]:
    url = _database_url()
    assert url
    tenant_id = uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
                    name="NoChxTenant",
                    billing_email="nc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="No Link Hotel",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Std",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            pid = prop.id
            rtid = rt.id
    await engine.dispose()
    return {"tenant_id": tenant_id, "property_id": pid, "room_type_id": rtid}


@pytest.fixture
def no_channex_ctx(channex_encrypt_env: None) -> dict[str, object]:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed_tenant_property_no_channex())


@pytest.mark.asyncio
async def test_push_stop_sell_happy_path(
    incremental_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = incremental_ctx["tenant_id"]
    pid = incremental_ctx["property_id"]
    rtid = incremental_ctx["room_type_id"]
    today = incremental_ctx["today"]
    mock_client = AsyncMock()
    mock_client.push_restrictions = AsyncMock(return_value={})
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_stop_sell(
            tid,  # type: ignore[arg-type]
            pid,
            rtid,
            [today.isoformat()],  # type: ignore[attr-defined]
        )
    mock_client.push_restrictions.assert_awaited()
    call = mock_client.push_restrictions.await_args
    assert call is not None
    batch = call[0][0]
    assert any(row.get("stop_sell") is True for row in batch)


@pytest.mark.asyncio
async def test_push_availability_skip_no_link(
    no_channex_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = no_channex_ctx["tenant_id"]
    pid = no_channex_ctx["property_id"]
    rtid = no_channex_ctx["room_type_id"]
    mock_client = AsyncMock()
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_availability(
            tid,  # type: ignore[arg-type]
            pid,
            rtid,
            ["2026-01-15"],
        )
    mock_client.push_availability.assert_not_called()


@pytest.mark.asyncio
async def test_push_availability_no_room_map(
    incremental_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = incremental_ctx["tenant_id"]
    pid = incremental_ctx["property_id"]
    today = incremental_ctx["today"]
    fake_rt = uuid4()
    mock_client = AsyncMock()
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_availability(
            tid,  # type: ignore[arg-type]
            pid,
            fake_rt,
            [today.isoformat()],  # type: ignore[attr-defined]
        )
    mock_client.push_availability.assert_not_called()


@pytest.mark.asyncio
async def test_push_availability_api_error(
    incremental_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = incremental_ctx["tenant_id"]
    pid = incremental_ctx["property_id"]
    rtid = incremental_ctx["room_type_id"]
    today = incremental_ctx["today"]
    mock_client = AsyncMock()
    mock_client.push_availability = AsyncMock(
        side_effect=ChannexApiError("boom", status_code=500),
    )
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_availability(
            tid,  # type: ignore[arg-type]
            pid,
            rtid,
            [today.isoformat()],  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_push_rates_skip_no_link(
    no_channex_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = no_channex_ctx["tenant_id"]
    pid = no_channex_ctx["property_id"]
    rtid = no_channex_ctx["room_type_id"]
    rpid = uuid4()
    mock_client = AsyncMock()
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_rates(
            tid,  # type: ignore[arg-type]
            pid,
            rtid,
            rpid,
            ["2026-01-15"],
        )
    mock_client.push_restrictions.assert_not_called()


@pytest.mark.asyncio
async def test_push_rates_api_error(
    incremental_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = incremental_ctx["tenant_id"]
    pid = incremental_ctx["property_id"]
    rtid = incremental_ctx["room_type_id"]
    rpid = incremental_ctx["rate_plan_id"]
    today = incremental_ctx["today"]
    mock_client = AsyncMock()
    mock_client.push_restrictions = AsyncMock(
        side_effect=ChannexApiError("rates failed", status_code=502),
    )
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_rates(
            tid,  # type: ignore[arg-type]
            pid,
            rtid,
            rpid,
            [today.isoformat()],  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_push_stop_sell_no_room_map(
    incremental_ctx: dict[str, object],
    channex_encrypt_env: None,
) -> None:
    tid = incremental_ctx["tenant_id"]
    pid = incremental_ctx["property_id"]
    today = incremental_ctx["today"]
    fake_rt = uuid4()
    mock_client = AsyncMock()
    with patch(
        "app.tasks.channex_incremental_ari._client_for_link",
        return_value=mock_client,
    ):
        await _run_push_channex_stop_sell(
            tid,  # type: ignore[arg-type]
            pid,
            fake_rt,
            [today.isoformat()],  # type: ignore[attr-defined]
        )
    mock_client.push_restrictions.assert_not_called()
