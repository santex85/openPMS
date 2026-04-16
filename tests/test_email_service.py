"""Tests for transactional email orchestration (Resend + email_logs)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.notifications.email_log import EmailLog
from app.services.channex_booking_service import ChannexIngestResult
from app.services.email_service import dispatch_channex_booking_emails, send_booking_email
from tests.db_seed import disable_row_security_for_test_seed


async def _with_fresh_engine(coro):
    """Run coroutine(engine) on a new AsyncEngine (avoids asyncio loop mismatch vs sync fixtures)."""
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")
    engine = create_async_engine(url)
    try:
        await coro(engine)
    finally:
        await engine.dispose()


def _database_url() -> str | None:
    import os

    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_send_booking_email_skips_without_api_key(
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]

    async def _body(engine: object) -> None:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.email_service.send_email", new_callable=AsyncMock) as send_m:
            with patch("app.services.email_service.get_settings") as gs:
                gs.return_value = MagicMock(resend_api_key="")
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(
                                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                            ),
                            {"tid": str(tid)},
                        )
                        await send_booking_email(
                            session,
                            tid,
                            "guest@example.com",
                            "Subject",
                            "<p>Hi</p>",
                            template_name="unit",
                        )
        send_m.assert_not_awaited()

    await _with_fresh_engine(_body)


@pytest.mark.asyncio
async def test_send_booking_email_logs_sent_and_failed(
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]

    async def _body(engine: object) -> None:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        with patch("app.services.email_service.get_settings") as gs:
            gs.return_value = MagicMock(resend_api_key="re_test_key")
            with patch("app.services.email_service.send_email", new_callable=AsyncMock) as send_m:
                send_m.return_value = "re_msg_1"
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(
                                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                            ),
                            {"tid": str(tid)},
                        )
                        await send_booking_email(
                            session,
                            tid,
                            "guest@example.com",
                            "Subject OK",
                            "<p>Hi</p>",
                            template_name="unit_sent",
                        )

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tid)},
                )
                sent_cnt = await session.scalar(
                    select(func.count())
                    .select_from(EmailLog)
                    .where(
                        EmailLog.tenant_id == tid,
                        EmailLog.template_name == "unit_sent",
                        EmailLog.status == "sent",
                    ),
                )
        assert sent_cnt == 1

        with patch("app.services.email_service.get_settings") as gs:
            gs.return_value = MagicMock(resend_api_key="re_test_key")
            with patch("app.services.email_service.send_email", new_callable=AsyncMock) as send_m:
                send_m.side_effect = RuntimeError("resend down")
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(
                                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                            ),
                            {"tid": str(tid)},
                        )
                        await send_booking_email(
                            session,
                            tid,
                            "guest@example.com",
                            "Subject Fail",
                            "<p>Hi</p>",
                            template_name="unit_failed",
                        )

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tid)},
                )
                fail_cnt = await session.scalar(
                    select(func.count())
                    .select_from(EmailLog)
                    .where(
                        EmailLog.tenant_id == tid,
                        EmailLog.template_name == "unit_failed",
                        EmailLog.status == "failed",
                    ),
                )
        assert fail_cnt == 1

    await _with_fresh_engine(_body)


@pytest.mark.asyncio
async def test_dispatch_channex_booking_emails_calls_helpers() -> None:
    tid = uuid4()
    bid = uuid4()
    pid = uuid4()
    factory = MagicMock()
    ingest = ChannexIngestResult(
        skip_idempotent=False,
        schedule_availability_push=False,
        tenant_id=tid,
        property_id=pid,
        room_type_id=None,
        date_strs=tuple(),
        success=True,
        email_confirmation_booking_id=bid,
        email_cancellation_booking_id=bid,
    )
    with patch(
        "app.services.email_service.run_send_booking_confirmation_task",
        new_callable=AsyncMock,
    ) as conf:
        with patch(
            "app.services.email_service.run_send_cancellation_email_task",
            new_callable=AsyncMock,
        ) as canc:
            await dispatch_channex_booking_emails(factory, ingest)
    conf.assert_awaited_once()
    canc.assert_awaited_once()


def test_post_send_invoice_rejects_invalid_guest_email(
    client: object,
    auth_headers: object,
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    bid: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    gid: UUID = tenant_isolation_booking_scenario["guest_id"]  # type: ignore[assignment]

    async def _set_invalid(engine: object) -> None:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await disable_row_security_for_test_seed(session)
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tid)},
                )
                await session.execute(
                    text(
                        "UPDATE guests SET email = :e "
                        "WHERE tenant_id = CAST(:tid AS uuid) AND id = CAST(:gid AS uuid)",
                    ),
                    {"e": "ann@x.invalid", "tid": str(tid), "gid": str(gid)},
                )

    asyncio.run(_with_fresh_engine(_set_invalid))

    hdrs = auth_headers(tid, role="owner")
    resp = client.post(
        f"/bookings/{bid}/send-invoice",
        headers=hdrs,
        json={},
    )
    assert resp.status_code == 422


def test_post_send_invoice_accepted_with_mocks(
    client: object,
    auth_headers: object,
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    tid: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    bid: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    hdrs = auth_headers(tid, role="owner")
    with patch("app.services.email_service.get_settings") as gs:
        gs.return_value = MagicMock(resend_api_key="re_test")
        with patch(
            "app.services.email_service.send_email",
            new_callable=AsyncMock,
            return_value="re_xyz",
        ):
            with patch(
                "app.services.email_service.generate_invoice_pdf",
                new_callable=AsyncMock,
                return_value=b"%PDF-1.4 test",
            ):
                resp = client.post(
                    f"/bookings/{bid}/send-invoice",
                    headers=hdrs,
                    json={},
                )
    assert resp.status_code == 202
