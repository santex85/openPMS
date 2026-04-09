"""Incremental Channex ARI tasks (availability / restrictions)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache, get_settings
from app.core.security import hash_password
from app.integrations.channex.crypto import encrypt_channex_api_key
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.models.core.room_type import RoomType
from app.tasks.channex_incremental_ari import (
    _run_push_channex_availability,
    _run_push_channex_rates,
)


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def channex_encrypt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("WEBHOOK_SECRET_FERNET_KEY", key)
    clear_settings_cache()


@pytest.fixture
def incremental_ctx(db_engine: object, channex_encrypt_env: None) -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    tenant_id = uuid4()
    owner_id = uuid4()
    cx_property_id = str(uuid4())
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    settings = get_settings()
    enc_key = encrypt_channex_api_key(settings, "incremental-test-key")

    async def _seed() -> dict[str, object]:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="IncrementalTenant",
                        billing_email="inc@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="o@inc.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Inc Hotel",
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

    return asyncio.run(_seed())


@pytest.mark.asyncio
async def test_incremental_availability_push(incremental_ctx: dict[str, object]) -> None:
    tid: object = incremental_ctx["tenant_id"]
    pid: object = incremental_ctx["property_id"]
    rtid: object = incremental_ctx["room_type_id"]
    today: object = incremental_ctx["today"]
    mock_client = AsyncMock()
    mock_client.push_availability = AsyncMock(return_value={})
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
    mock_client.push_availability.assert_awaited()
    call_args = mock_client.push_availability.await_args
    assert call_args is not None
    values = call_args[0][0]
    assert len(values) == 1
    assert values[0]["availability"] == 3


@pytest.mark.asyncio
async def test_incremental_rates_sends_decimal_string_and_restrictions(
    incremental_ctx: dict[str, object],
) -> None:
    tid: object = incremental_ctx["tenant_id"]
    pid: object = incremental_ctx["property_id"]
    rtid: object = incremental_ctx["room_type_id"]
    rpid: object = incremental_ctx["rate_plan_id"]
    today: object = incremental_ctx["today"]
    mock_client = AsyncMock()
    mock_client.push_restrictions = AsyncMock(return_value={})
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
    mock_client.push_restrictions.assert_awaited()
    values = mock_client.push_restrictions.await_args[0][0]
    assert len(values) == 1
    assert values[0]["rate"] == "199.50"
    assert values[0]["stop_sell"] is False
    assert values[0]["min_stay_arrival"] == 2
    assert values[0]["max_stay"] == 14


def test_put_rates_bulk_enqueues_channex_rates_delay(
    client: object,
    auth_headers: object,
    db_engine: object,
    channex_encrypt_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    assert isinstance(client, TestClient)

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_incremental_ari.push_channex_rates.delay",
        mock_delay,
    )
    monkeypatch.setattr(
        "app.services.webhook_runner.run_rate_updated_webhooks",
        lambda *a, **k: None,
    )

    tenant_id = uuid4()
    owner_id = uuid4()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    settings = get_settings()
    enc = encrypt_channex_api_key(settings, "k")

    async def _seed() -> tuple[str, str, str, str]:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="RTenant",
                        billing_email="r@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="own@example.com",
                        password_hash=hash_password("x"),
                        full_name="O",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="P",
                    timezone="UTC",
                    currency="EUR",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                rt = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="R",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add(rt)
                await session.flush()
                rp = RatePlan(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="X",
                    cancellation_policy="none",
                )
                session.add(rp)
                await session.flush()
                session.add(
                    ChannexPropertyLink(
                        tenant_id=tenant_id,
                        property_id=prop.id,
                        channex_property_id=str(uuid4()),
                        channex_api_key=enc,
                        channex_env="production",
                        status="active",
                    ),
                )
                d0 = date(2027, 6, 1)
                return str(prop.id), str(rt.id), str(rp.id), d0.isoformat()

    prop_id, rt_id, rp_id, d_iso = asyncio.run(_seed())
    headers = auth_headers(tenant_id, user_id=owner_id, role="owner")

    body = {
        "segments": [
            {
                "room_type_id": rt_id,
                "rate_plan_id": rp_id,
                "start_date": d_iso,
                "end_date": d_iso,
                "price": "100.00",
                "stop_sell": True,
                "min_stay_arrival": 3,
            },
        ],
    }
    res = client.put("/rates/bulk", json=body, headers=headers)
    assert res.status_code == 200, res.text
    mock_delay.assert_called_once_with(
        str(tenant_id),
        prop_id,
        rt_id,
        rp_id,
        [d_iso],
    )
