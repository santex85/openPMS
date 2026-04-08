"""Channex sync, webhook inbound, activate (webhook), disconnect (delete webhook)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache, get_settings
from app.core.security import hash_password
from app.integrations.channex.crypto import encrypt_channex_api_key
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.integrations.channex_webhook_log import ChannexWebhookLog
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.tasks.channex_ari_sync import _run_channex_full_ari_sync
from app.tasks.channex_webhook_task import _run_channex_process_webhook


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_channex_property(
    db_engine: object,
    *,
    status: str,
    channex_webhook_id: str | None,
) -> dict[str, object]:
    """Tenant + property + Channex link, room/rate maps + one rate row (for ARI sync)."""
    tenant_id = uuid4()
    owner_id = uuid4()
    cx_property_id = str(uuid4())
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    settings = get_settings()
    enc_key = encrypt_channex_api_key(settings, "test-channex-user-api-key")

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="ChannexTestTenant",
                    billing_email="cx-test@example.com",
                    status="active",
                ),
            )
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="owner@cx-test.example.com",
                    password_hash=hash_password("secret"),
                    full_name="Owner",
                    role="owner",
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="CX Hotel",
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
                name="Deluxe",
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
                channex_webhook_id=channex_webhook_id,
                channex_api_key=enc_key,
                channex_env="production",
                status=status,
            )
            session.add(link)
            await session.flush()
            rtm = ChannexRoomTypeMap(
                tenant_id=tenant_id,
                property_link_id=link.id,
                room_type_id=rt.id,
                channex_room_type_id=str(uuid4()),
                channex_room_type_name="Deluxe CX",
            )
            session.add(rtm)
            await session.flush()
            rpm = ChannexRatePlanMap(
                tenant_id=tenant_id,
                room_type_map_id=rtm.id,
                rate_plan_id=rp.id,
                channex_rate_plan_id=str(uuid4()),
                channex_rate_plan_name="BAR CX",
            )
            session.add(rpm)
            today = datetime.now(UTC).date()
            session.add(
                Rate(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=today,
                    price=Decimal("120.00"),
                ),
            )
            link_id = link.id
            property_id = prop.id

    return {
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "property_id": property_id,
        "link_id": link_id,
        "cx_property_id": cx_property_id,
        "room_type_map_id": rtm.id,
        "rate_plan_map_id": rpm.id,
    }


@pytest.fixture
def channex_active_ctx(
    db_engine: object,
    channex_encrypt_env: None,
) -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(
        _seed_channex_property(
            db_engine,
            status="active",
            channex_webhook_id=None,
        ),
    )


@pytest.fixture
def channex_pending_ctx(
    db_engine: object,
    channex_encrypt_env: None,
) -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(
        _seed_channex_property(
            db_engine,
            status="pending",
            channex_webhook_id=None,
        ),
    )


@pytest.fixture
def channex_with_webhook_ctx(
    db_engine: object,
    channex_encrypt_env: None,
) -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    return asyncio.run(
        _seed_channex_property(
            db_engine,
            status="active",
            channex_webhook_id=str(uuid4()),
        ),
    )


@pytest.fixture
def channex_encrypt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stable Fernet so encrypt_channex_api_key works in tests."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("WEBHOOK_SECRET_FERNET_KEY", key)
    clear_settings_cache()


@pytest.mark.asyncio
async def test_channex_full_ari_sync_pushes_and_updates_last_sync(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    tenant_id: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = channex_active_ctx["property_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]

    mock_client = AsyncMock()
    mock_client.push_availability = AsyncMock()
    mock_client.push_restrictions = AsyncMock()

    with patch(
        "app.tasks.channex_ari_sync._client_for_link",
        return_value=mock_client,
    ):
        await _run_channex_full_ari_sync(tenant_id, property_id)

    assert mock_client.push_availability.await_count >= 1
    assert mock_client.push_restrictions.await_count >= 1

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        assert link.last_sync_at is not None
        assert link.error_message is None


def test_post_channex_sync_accepted_enqueues_task(
    client: object,
    auth_headers: object,
    channex_active_ctx: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    pid: UUID = channex_active_ctx["property_id"]  # type: ignore[assignment]
    oid: UUID = channex_active_ctx["owner_id"]  # type: ignore[assignment]
    headers = auth_headers(tid, user_id=oid, role="owner")

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_ari_sync.channex_full_ari_sync.delay",
        mock_delay,
    )

    res = client.post(
        "/channex/sync",
        params={"property_id": str(pid)},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body.get("detail") == "Sync queued"
    mock_delay.assert_called_once_with(str(tid), str(pid))


def test_post_channex_sync_fails_when_not_active(
    client: object,
    auth_headers: object,
    channex_pending_ctx: dict[str, object],
) -> None:
    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_pending_ctx["tenant_id"]  # type: ignore[assignment]
    pid: UUID = channex_pending_ctx["property_id"]  # type: ignore[assignment]
    oid: UUID = channex_pending_ctx["owner_id"]  # type: ignore[assignment]
    headers = auth_headers(tid, user_id=oid, role="owner")

    res = client.post(
        "/channex/sync",
        params={"property_id": str(pid)},
        headers=headers,
    )
    assert res.status_code == 409


def test_activate_sets_webhook_id(
    client: object,
    auth_headers: object,
    channex_pending_ctx: dict[str, object],
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("CHANNEX_WEBHOOK_URL", "https://openpms.example/webhooks/channex")
    clear_settings_cache()

    assert isinstance(client, TestClient)
    tid: UUID = channex_pending_ctx["tenant_id"]  # type: ignore[assignment]
    pid: UUID = channex_pending_ctx["property_id"]  # type: ignore[assignment]
    oid: UUID = channex_pending_ctx["owner_id"]  # type: ignore[assignment]
    link_id: UUID = channex_pending_ctx["link_id"]  # type: ignore[assignment]
    headers = auth_headers(tid, user_id=oid, role="owner")

    webhook_uuid = str(uuid4())
    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_ari_sync.channex_full_ari_sync.delay",
        mock_delay,
    )

    with patch("app.services.channex_service.ChannexClient") as MockCx:
        inst = MockCx.return_value
        inst.create_webhook = AsyncMock(
            return_value={"data": {"id": webhook_uuid, "type": "webhook"}},
        )

        res = client.post(
            "/channex/activate",
            params={"property_id": str(pid)},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        inst.create_webhook.assert_awaited()

    mock_delay.assert_called_once_with(str(tid), str(pid))

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _read() -> None:
        async with factory() as session:
            link = await session.get(ChannexPropertyLink, link_id)
            assert link is not None
            assert link.status == "active"
            assert link.channex_webhook_id == webhook_uuid

    asyncio.run(_read())


def test_disconnect_calls_delete_webhook(
    client: object,
    auth_headers: object,
    channex_with_webhook_ctx: dict[str, object],
    db_engine: object,
) -> None:
    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_with_webhook_ctx["tenant_id"]  # type: ignore[assignment]
    pid: UUID = channex_with_webhook_ctx["property_id"]  # type: ignore[assignment]
    oid: UUID = channex_with_webhook_ctx["owner_id"]  # type: ignore[assignment]
    headers = auth_headers(tid, user_id=oid, role="owner")

    with patch("app.services.channex_service.ChannexClient") as MockCx:
        inst = MockCx.return_value
        inst.delete_webhook = AsyncMock()
        res = client.post(
            "/channex/disconnect",
            params={"property_id": str(pid)},
            headers=headers,
        )
        assert res.status_code == 204, res.text
        inst.delete_webhook.assert_awaited()

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _gone() -> None:
        async with factory() as session:
            res = await session.execute(
                select(ChannexPropertyLink).where(ChannexPropertyLink.property_id == pid),
            )
            assert res.scalar_one_or_none() is None

    asyncio.run(_gone())


def test_inbound_webhook_persists_log_without_auth(
    client: object,
    channex_active_ctx: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    db_engine: object,
) -> None:
    from starlette.testclient import TestClient

    monkeypatch.delenv("CHANNEX_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHANNEX_WEBHOOK_VERIFY_CHANNEX_IPS", "false")
    clear_settings_cache()

    assert isinstance(client, TestClient)
    cx_pid: str = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_webhook_task.channex_process_webhook.delay",
        mock_delay,
    )

    payload = {"event": "ari", "property_id": cx_pid}
    res = client.post(
        "/webhooks/channex",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text
    assert res.json().get("status") == "ok"
    mock_delay.assert_called_once()

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _check() -> None:
        async with factory() as session:
            q = await session.execute(
                select(ChannexWebhookLog)
                .where(ChannexWebhookLog.payload.contains({"property_id": cx_pid}))
                .order_by(ChannexWebhookLog.created_at.desc())
                .limit(1),
            )
            row = q.scalars().first()
            assert row is not None
            assert row.event_type == "ari"
            assert row.payload.get("property_id") == cx_pid

    asyncio.run(_check())


def test_inbound_webhook_rejects_bad_hmac(
    client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    secret = "wh-secret-test"
    monkeypatch.setenv("CHANNEX_WEBHOOK_SECRET", secret)
    clear_settings_cache()

    assert isinstance(client, TestClient)
    body = b'{"event":"ari"}'
    res = client.post(
        "/webhooks/channex",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Channex-Signature": "deadbeef",
        },
    )
    assert res.status_code == 401


def test_inbound_webhook_accepts_valid_hmac(
    client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    secret = "wh-secret-test"
    monkeypatch.setenv("CHANNEX_WEBHOOK_SECRET", secret)
    clear_settings_cache()

    assert isinstance(client, TestClient)
    body = b'{"event":"ping"}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_webhook_task.channex_process_webhook.delay",
        mock_delay,
    )

    res = client.post(
        "/webhooks/channex",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Channex-Signature": sig,
        },
    )
    assert res.status_code == 200
    mock_delay.assert_called_once()


@pytest.mark.asyncio
async def test_process_webhook_marks_ari_processed(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    cx_pid: str = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    log_id: UUID
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = ChannexWebhookLog(
                tenant_id=tid,
                event_type="ari",
                payload={"event": "ari", "property_id": cx_pid},
                signature=None,
                ip_address="127.0.0.1",
                processed=False,
            )
            session.add(row)
            await session.flush()
            log_id = row.id

    await _run_channex_process_webhook(log_id)

    async with factory() as session:
        row2 = await session.get(ChannexWebhookLog, log_id)
        assert row2 is not None
        assert row2.processed is True
