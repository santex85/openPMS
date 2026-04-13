"""GET /channex/revisions/failed and POST .../retry (Sequence 240–241)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.services.channex_booking_service import ChannexIngestResult
from app.tasks.channex_booking_retry import _run_channex_retry_booking_revision

from tests.test_channex_webhook_sync import _database_url


def _revision_flat(
    *,
    revision_id: str,
    booking_id: str,
    cx_room_type_id: str,
    cx_rate_plan_id: str,
    ci: date,
    co: date,
) -> dict[str, object]:
    return {
        "id": revision_id,
        "booking_id": booking_id,
        "status": "new",
        "amount": "100.00",
        "currency": "USD",
        "arrival_date": ci.isoformat(),
        "departure_date": co.isoformat(),
        "customer": {
            "name": "Ann",
            "surname": "Bee",
            "mail": f"ann-{booking_id[:8]}@retry.test",
            "phone": "+1999000111",
        },
        "rooms": [
            {
                "room_type_id": cx_room_type_id,
                "rate_plan_id": cx_rate_plan_id,
                "checkin_date": ci.isoformat(),
                "checkout_date": co.isoformat(),
            },
        ],
        "channel_id": "booking.com",
    }


@pytest.mark.asyncio
async def test_get_failed_revisions_lists_error_row(
    db_engine: object,
    channex_active_ctx: dict[str, object],
    client: object,
    auth_headers: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]
    oid: UUID = channex_active_ctx["owner_id"]  # type: ignore[assignment]
    prop_id: UUID = channex_active_ctx["property_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])

    ci = (datetime.now(UTC).date() + timedelta(days=50))
    co = ci + timedelta(days=2)
    openpms_rev_id = uuid4()
    cx_rev_id = str(uuid4())
    book_id = str(uuid4())
    flat = _revision_flat(
        revision_id=cx_rev_id,
        booking_id=book_id,
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                ChannexBookingRevision(
                    id=openpms_rev_id,
                    tenant_id=tid,
                    property_link_id=link_id,
                    channex_revision_id=cx_rev_id,
                    channex_booking_id=book_id,
                    status="new",
                    channel_code="booking.com",
                    payload=flat,
                    processing_status="error",
                    error_message="test error",
                ),
            )

    headers = auth_headers(tid, user_id=oid, role="owner")
    res = client.get("/channex/revisions/failed", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    match = next((i for i in body["items"] if i["id"] == str(openpms_rev_id)), None)
    assert match is not None
    assert match["channex_revision_id"] == cx_rev_id
    assert match["property_id"] == str(prop_id)
    assert match["error_message"] == "test error"

    res_f = client.get(
        "/channex/revisions/failed",
        headers=headers,
        params={"property_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert res_f.status_code == 200, res_f.text
    ids_wrong_prop = {i["id"] for i in res_f.json()["items"]}
    assert str(openpms_rev_id) not in ids_wrong_prop


def test_post_retry_accepts_and_enqueues(
    client: object,
    auth_headers: object,
    channex_active_ctx: dict[str, object],
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]
    oid: UUID = channex_active_ctx["owner_id"]  # type: ignore[assignment]

    ci = (datetime.now(UTC).date() + timedelta(days=55))
    co = ci + timedelta(days=2)
    openpms_rev_id = uuid4()
    cx_rev_id = str(uuid4())
    book_id = str(uuid4())
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    flat = _revision_flat(
        revision_id=cx_rev_id,
        booking_id=book_id,
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    async def _seed() -> None:
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tid)},
                )
                session.add(
                    ChannexBookingRevision(
                        id=openpms_rev_id,
                        tenant_id=tid,
                        property_link_id=link_id,
                        channex_revision_id=cx_rev_id,
                        channex_booking_id=book_id,
                        status="new",
                        channel_code="booking.com",
                        payload=flat,
                        processing_status="error",
                        error_message="retry me",
                    ),
                )

    asyncio.run(_seed())

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "app.tasks.channex_booking_retry.channex_retry_booking_revision.delay",
        mock_delay,
    )

    headers = auth_headers(tid, user_id=oid, role="owner")
    res = client.post(
        f"/channex/revisions/{openpms_rev_id}/retry",
        headers=headers,
    )
    assert res.status_code == 202, res.text
    assert res.json() == {"status": "queued"}
    mock_delay.assert_called_once_with(str(openpms_rev_id), str(tid))


def test_post_retry_404_unknown_revision(
    client: object,
    auth_headers: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    oid: UUID = channex_active_ctx["owner_id"]  # type: ignore[assignment]
    headers = auth_headers(tid, user_id=oid, role="owner")
    res = client.post(
        f"/channex/revisions/{uuid4()}/retry",
        headers=headers,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_post_retry_400_when_not_error(
    db_engine: object,
    channex_active_ctx: dict[str, object],
    client: object,
    auth_headers: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    from starlette.testclient import TestClient

    assert isinstance(client, TestClient)
    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]
    oid: UUID = channex_active_ctx["owner_id"]  # type: ignore[assignment]

    ci = (datetime.now(UTC).date() + timedelta(days=60))
    co = ci + timedelta(days=2)
    openpms_rev_id = uuid4()
    cx_rev_id = str(uuid4())
    book_id = str(uuid4())
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    flat = _revision_flat(
        revision_id=cx_rev_id,
        booking_id=book_id,
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                ChannexBookingRevision(
                    id=openpms_rev_id,
                    tenant_id=tid,
                    property_link_id=link_id,
                    channex_revision_id=cx_rev_id,
                    channex_booking_id=book_id,
                    status="new",
                    channel_code="booking.com",
                    payload=flat,
                    processing_status="done",
                    error_message=None,
                ),
            )

    headers = auth_headers(tid, user_id=oid, role="owner")
    res = client.post(
        f"/channex/revisions/{openpms_rev_id}/retry",
        headers=headers,
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_retry_task_acks_after_successful_ingest(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]
    prop_id: UUID = channex_active_ctx["property_id"]  # type: ignore[assignment]

    ci = (datetime.now(UTC).date() + timedelta(days=65))
    co = ci + timedelta(days=2)
    openpms_rev_id = uuid4()
    cx_rev_id = str(uuid4())
    book_id = str(uuid4())
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    flat = _revision_flat(
        revision_id=cx_rev_id,
        booking_id=book_id,
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                ChannexBookingRevision(
                    id=openpms_rev_id,
                    tenant_id=tid,
                    property_link_id=link_id,
                    channex_revision_id=cx_rev_id,
                    channex_booking_id=book_id,
                    status="new",
                    channel_code="booking.com",
                    payload=flat,
                    processing_status="error",
                    error_message="retry task test",
                ),
            )

    mock_client = AsyncMock()
    mock_client.acknowledge_revision = AsyncMock(return_value={})
    ingest_result = ChannexIngestResult(
        skip_idempotent=False,
        schedule_availability_push=False,
        tenant_id=tid,
        property_id=prop_id,
        room_type_id=None,
        date_strs=tuple(),
        success=True,
    )

    with patch(
        "app.tasks.channex_booking_retry._client_for_link",
        return_value=mock_client,
    ):
        with patch(
            "app.tasks.channex_booking_retry.ingest_channex_booking",
            new_callable=AsyncMock,
            return_value=ingest_result,
        ):
            await _run_channex_retry_booking_revision(openpms_rev_id, tid)

    mock_client.acknowledge_revision.assert_awaited_once_with(cx_rev_id)


@pytest.mark.asyncio
async def test_retry_task_skips_ack_when_ingest_fails(
    db_engine: object,
    channex_active_ctx: dict[str, object],
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")

    tid: UUID = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id: UUID = channex_active_ctx["link_id"]  # type: ignore[assignment]
    prop_id: UUID = channex_active_ctx["property_id"]  # type: ignore[assignment]

    ci = (datetime.now(UTC).date() + timedelta(days=70))
    co = ci + timedelta(days=2)
    openpms_rev_id = uuid4()
    cx_rev_id = str(uuid4())
    book_id = str(uuid4())
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    flat = _revision_flat(
        revision_id=cx_rev_id,
        booking_id=book_id,
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                ChannexBookingRevision(
                    id=openpms_rev_id,
                    tenant_id=tid,
                    property_link_id=link_id,
                    channex_revision_id=cx_rev_id,
                    channex_booking_id=book_id,
                    status="new",
                    channel_code="booking.com",
                    payload=flat,
                    processing_status="error",
                    error_message="still bad",
                ),
            )

    mock_client = AsyncMock()
    mock_client.acknowledge_revision = AsyncMock(return_value={})
    ingest_result = ChannexIngestResult(
        skip_idempotent=False,
        schedule_availability_push=False,
        tenant_id=tid,
        property_id=prop_id,
        room_type_id=None,
        date_strs=tuple(),
        success=False,
    )

    with patch(
        "app.tasks.channex_booking_retry._client_for_link",
        return_value=mock_client,
    ):
        with patch(
            "app.tasks.channex_booking_retry.ingest_channex_booking",
            new_callable=AsyncMock,
            return_value=ingest_result,
        ):
            await _run_channex_retry_booking_revision(openpms_rev_id, tid)

    mock_client.acknowledge_revision.assert_not_called()
