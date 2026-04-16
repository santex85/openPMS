"""TZ-16 seq 40–41: property email_settings API and booking email_logs read API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.notifications.email_log import EmailLog


def _database_url() -> str | None:
    import os

    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def test_email_settings_get_404_put_get(
    client: object,
    auth_headers: object,
    smoke_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    pid: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    hdrs = auth_headers(tid, role="owner")

    r0 = client.get(f"/properties/{pid}/email-settings", headers=hdrs)
    assert r0.status_code == 404

    body = {
        "sender_name": "Smoke Hotel",
        "reply_to": "frontdesk@example.com",
        "logo_url": "https://example.com/logo.png",
        "locale": "en",
    }
    r1 = client.put(f"/properties/{pid}/email-settings", headers=hdrs, json=body)
    assert r1.status_code == 200
    data = r1.json()
    assert data["sender_name"] == body["sender_name"]
    assert data["reply_to"] == body["reply_to"]
    assert data["locale"] == "en"

    r2 = client.get(f"/properties/{pid}/email-settings", headers=hdrs)
    assert r2.status_code == 200
    assert r2.json()["id"] == data["id"]


def test_booking_email_logs_empty_and_sorted(
    client: object,
    auth_headers: object,
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid_a: UUID = tenant_isolation_booking_scenario["tenant_a"]  # type: ignore[assignment]
    bid: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    pid: UUID = tenant_isolation_booking_scenario["property_id"]  # type: ignore[assignment]
    hdrs = auth_headers(tid_a, role="owner")

    r0 = client.get(f"/bookings/{bid}/email-logs", headers=hdrs)
    assert r0.status_code == 200
    assert r0.json() == []

    t_old = datetime.now(UTC) - timedelta(hours=2)
    t_new = datetime.now(UTC) - timedelta(hours=1)
    log_old_id = uuid4()
    log_new_id = uuid4()

    async def _seed_logs() -> None:
        url = _database_url()
        assert url
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                        ),
                        {"tid": str(tid_a)},
                    )
                    session.add(
                        EmailLog(
                            id=log_old_id,
                            tenant_id=tid_a,
                            property_id=pid,
                            booking_id=bid,
                            to_address="a@example.com",
                            template_name="t_old",
                            subject="S1",
                            status="sent",
                            resend_id="r1",
                            error_message=None,
                            sent_at=t_old,
                        ),
                    )
                    session.add(
                        EmailLog(
                            id=log_new_id,
                            tenant_id=tid_a,
                            property_id=pid,
                            booking_id=bid,
                            to_address="b@example.com",
                            template_name="t_new",
                            subject="S2",
                            status="failed",
                            resend_id=None,
                            error_message="x",
                            sent_at=t_new,
                        ),
                    )
        finally:
            await engine.dispose()

    asyncio.run(_seed_logs())

    r1 = client.get(f"/bookings/{bid}/email-logs", headers=hdrs)
    assert r1.status_code == 200
    arr = r1.json()
    assert len(arr) == 2
    assert arr[0]["id"] == str(log_new_id)
    assert arr[0]["template_name"] == "t_new"
    assert arr[1]["id"] == str(log_old_id)


def test_post_property_email_test_404_without_settings(
    client: object,
    auth_headers_user: object,
    smoke_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    oid: UUID = smoke_scenario["owner_id"]  # type: ignore[assignment]
    pid: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    hdrs = auth_headers_user(tid, oid, role="owner")
    with patch(
        "app.api.routes.properties.get_settings",
        return_value=MagicMock(resend_api_key="re_test_key"),
    ):
        r = client.post(f"/properties/{pid}/email/test", headers=hdrs)
    assert r.status_code == 404


def test_post_property_email_test_422_and_202(
    client: object,
    auth_headers_user: object,
    smoke_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    oid: UUID = smoke_scenario["owner_id"]  # type: ignore[assignment]
    pid: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    hdrs = auth_headers_user(tid, oid, role="owner")
    body = {
        "sender_name": "Test Sender",
        "reply_to": "reply@example.com",
        "logo_url": None,
        "locale": "en",
    }
    assert (
        client.put(
            f"/properties/{pid}/email-settings",
            headers=hdrs,
            json=body,
        ).status_code
        == 200
    )

    with patch(
        "app.api.routes.properties.get_settings",
        return_value=MagicMock(resend_api_key=""),
    ):
        r_422 = client.post(f"/properties/{pid}/email/test", headers=hdrs)
    assert r_422.status_code == 422

    with patch(
        "app.services.email_service.send_email",
        new_callable=AsyncMock,
        return_value="re_msg_test",
    ):
        with patch(
            "app.api.routes.properties.get_settings",
            return_value=MagicMock(resend_api_key="re_secret"),
        ):
            r_202 = client.post(f"/properties/{pid}/email/test", headers=hdrs)
    assert r_202.status_code == 202


def test_booking_email_logs_not_visible_cross_tenant(
    client: object,
    auth_headers: object,
    tenant_isolation_booking_scenario: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid_b: UUID = tenant_isolation_booking_scenario["tenant_b"]  # type: ignore[assignment]
    bid: UUID = tenant_isolation_booking_scenario["booking_id"]  # type: ignore[assignment]
    hdrs_b = auth_headers(tid_b, role="owner")
    r = client.get(f"/bookings/{bid}/email-logs", headers=hdrs_b)
    assert r.status_code == 404
