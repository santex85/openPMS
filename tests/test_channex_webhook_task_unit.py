"""Extra branches for channex_webhook_task (events, API errors)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.channex.client import ChannexApiError
from app.models.integrations.channex_webhook_log import ChannexWebhookLog
from app.tasks.channex_webhook_task import _run_channex_process_webhook

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url


@pytest.mark.asyncio
async def test_process_webhook_unknown_event_still_marks_processed(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    cx_pid: str = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    log_id: UUID
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = ChannexWebhookLog(
                tenant_id=tid,
                event_type="weird",
                payload={"event": "not_a_booking_event", "property_id": cx_pid},
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


@pytest.mark.asyncio
async def test_process_webhook_no_property_logs_and_processed(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    log_id: UUID
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = ChannexWebhookLog(
                tenant_id=tid,
                event_type="booking",
                payload={"event": "booking", "payload": {"id": str(uuid4())}},
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


@pytest.mark.asyncio
async def test_process_webhook_booking_fetch_failure_still_marks_processed(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    cx_pid: str = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]
    revision = str(uuid4())

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    log_id: UUID
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = ChannexWebhookLog(
                tenant_id=tid,
                event_type="booking",
                payload={
                    "event": "booking_new",
                    "property_id": cx_pid,
                    "payload": {"id": revision},
                },
                signature=None,
                ip_address="127.0.0.1",
                processed=False,
            )
            session.add(row)
            await session.flush()
            log_id = row.id

    mock_client = AsyncMock()
    mock_client.get_booking_revision_raw = AsyncMock(
        side_effect=ChannexApiError("not found", status_code=404),
    )
    with patch(
        "app.tasks.channex_webhook_task._client_for_link",
        return_value=mock_client,
    ):
        await _run_channex_process_webhook(log_id)

    async with factory() as session:
        row2 = await session.get(ChannexWebhookLog, log_id)
    assert row2 is not None
    assert row2.processed is True
    mock_client.get_booking_revision_raw.assert_awaited()
