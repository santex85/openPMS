"""Unit tests for channex_booking_service helpers and ingest edge cases."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.channex_booking_service import (
    _decimal_amount,
    _nightly_prices_from_total,
    _parse_iso_date,
    ingest_channex_booking,
)


def test_nightly_prices_empty_nights() -> None:
    assert _nightly_prices_from_total(Decimal("100"), []) == []


def test_nightly_prices_splits_total_across_nights() -> None:
    nights = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    out = _nightly_prices_from_total(Decimal("10.00"), nights)
    assert len(out) == 3
    assert sum(p for _, p in out) == Decimal("10.00")


def test_parse_iso_date_datetime_string() -> None:
    assert _parse_iso_date("2026-05-01T12:00:00Z") == date(2026, 5, 1)


def test_decimal_amount_handles_types() -> None:
    assert _decimal_amount(None) == Decimal("0.00")
    assert _decimal_amount("") == Decimal("0.00")
    assert _decimal_amount(12.5) == Decimal("12.50")
    assert _decimal_amount("9.99") == Decimal("9.99")


@pytest.mark.asyncio
async def test_ingest_invalid_json_returns_empty_schedule(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    from sqlalchemy import select

    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from tests.test_channex_webhook_sync import _seed_channex_property

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            link = await session.scalar(
                select(ChannexPropertyLink).where(
                    ChannexPropertyLink.id == ctx["link_id"],
                ),
            )
            assert link is not None
            out = await ingest_channex_booking(
                session,
                tid,
                link,
                {"not": "a valid revision payload"},
            )
    assert out.schedule_availability_push is False
    assert out.room_type_id is None


@pytest.mark.asyncio
async def test_ingest_missing_revision_id_returns_empty(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    from sqlalchemy import select

    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from tests.test_channex_webhook_sync import _seed_channex_property

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    minimal = {
        "id": "",
        "booking_id": str(uuid4()),
        "status": "confirmed",
        "rooms": [],
    }
    async with factory() as session:
        async with session.begin():
            link = await session.scalar(
                select(ChannexPropertyLink).where(
                    ChannexPropertyLink.id == ctx["link_id"],
                ),
            )
            assert link is not None
            out = await ingest_channex_booking(session, tid, link, minimal)
    assert out.skip_idempotent is False
    assert out.date_strs == ()


@pytest.mark.asyncio
async def test_claim_revision_retry_error_status_reopens(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    """Second ingest with same revision_id and processing_status=error reclaims row."""
    from sqlalchemy import select

    from app.models.integrations.channex_booking_revision import ChannexBookingRevision
    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from app.services.channex_booking_service import _claim_revision_row
    from app.integrations.channex.schemas import ChannexBookingRevisionPayload
    from tests.test_channex_webhook_sync import _seed_channex_property

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    revision_id = str(uuid4())
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    base_room = {
        "room_type_id": ctx["channex_room_type_id"],
        "rate_plan_id": ctx["channex_rate_plan_id"],
        "checkin_date": "2026-08-01",
        "checkout_date": "2026-08-03",
    }
    flat = {
        "id": revision_id,
        "booking_id": str(uuid4()),
        "status": "confirmed",
        "amount": "100.00",
        "rooms": [base_room],
        "customer": {"name": "A", "surname": "B", "mail": "a@example.com", "phone": "+1"},
    }
    payload = ChannexBookingRevisionPayload.model_validate(flat)

    async with factory() as session:
        async with session.begin():
            link = await session.scalar(
                select(ChannexPropertyLink).where(
                    ChannexPropertyLink.id == ctx["link_id"],
                ),
            )
            assert link is not None
            rev = await _claim_revision_row(
                session,
                tid,
                link,
                revision_id,
                flat,
                payload,
            )
            assert rev != "skip"
            rev.processing_status = "error"
            rev.error_message = "previous failure"

    async with factory() as session:
        async with session.begin():
            link = await session.scalar(
                select(ChannexPropertyLink).where(
                    ChannexPropertyLink.id == ctx["link_id"],
                ),
            )
            assert link is not None
            rev2 = await _claim_revision_row(
                session,
                tid,
                link,
                revision_id,
                flat,
                payload,
            )
            assert rev2 != "skip"
            row = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == revision_id,
                ),
            )
    assert row is not None
    assert row.processing_status == "processing"


@pytest.mark.asyncio
async def test_ingest_cancelled_without_local_booking_marks_error(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    from sqlalchemy import select

    from app.models.integrations.channex_booking_revision import ChannexBookingRevision
    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from tests.test_channex_webhook_sync import _seed_channex_property

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    ext_id = str(uuid4())
    revision_id = str(uuid4())
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    flat = {
        "id": revision_id,
        "booking_id": ext_id,
        "status": "cancelled",
        "amount": "0",
        "rooms": [
            {
                "room_type_id": ctx["channex_room_type_id"],
                "rate_plan_id": ctx["channex_rate_plan_id"],
                "checkin_date": "2026-09-01",
                "checkout_date": "2026-09-02",
            },
        ],
        "customer": {"name": "X", "surname": "Y", "mail": "x@example.com", "phone": "+1"},
    }
    async with factory() as session:
        async with session.begin():
            link = await session.scalar(
                select(ChannexPropertyLink).where(
                    ChannexPropertyLink.id == ctx["link_id"],
                ),
            )
            assert link is not None
            out = await ingest_channex_booking(session, tid, link, flat)
            assert out.schedule_availability_push is False
            row = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == revision_id,
                ),
            )
    assert row is not None
    assert row.processing_status == "error"
    assert "No OpenPMS booking" in (row.error_message or "")


@pytest.mark.asyncio
async def test_ingest_new_insufficient_inventory_marks_revision_error(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from sqlalchemy import select, text

    from app.db.rls_session import tenant_transaction_session
    from app.models.integrations.channex_booking_revision import ChannexBookingRevision
    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from app.services.availability_lock import InsufficientInventoryError
    from tests.test_channex_booking_ingestion import _revision_flat
    from tests.test_channex_webhook_sync import _database_url, _seed_channex_property

    if not _database_url():
        pytest.skip("DATABASE_URL required")

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    link_id = ctx["link_id"]
    cx_rt = str(ctx["channex_room_type_id"])
    cx_rp = str(ctx["channex_rate_plan_id"])
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    ci = datetime.now(UTC).date() + timedelta(days=50)
    co = ci + timedelta(days=2)
    rev_id = str(uuid4())
    book_id = str(uuid4())
    flat = _revision_flat(
        revision_id=rev_id,
        booking_id=book_id,
        status="new",
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    with patch(
        "app.services.channex_booking_service.lock_and_validate_availability",
        side_effect=InsufficientInventoryError("no availability"),
    ):
        async with tenant_transaction_session(factory, tid) as session:
            link = await session.get(ChannexPropertyLink, link_id)
            assert link is not None
            out = await ingest_channex_booking(session, tid, link, flat)

    assert out.schedule_availability_push is False
    assert out.skip_idempotent is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            row = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == rev_id,
                ),
            )
    assert row is not None
    assert row.processing_status == "error"
    assert (row.error_message or "").startswith("overbooking:")


@pytest.mark.asyncio
async def test_ingest_new_ledger_not_seeded_marks_revision_error(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from sqlalchemy import select, text

    from app.db.rls_session import tenant_transaction_session
    from app.models.integrations.channex_booking_revision import ChannexBookingRevision
    from app.models.integrations.channex_property_link import ChannexPropertyLink
    from app.services.availability_lock import LedgerNotSeededError
    from tests.test_channex_booking_ingestion import _revision_flat
    from tests.test_channex_webhook_sync import _database_url, _seed_channex_property

    if not _database_url():
        pytest.skip("DATABASE_URL required")

    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    tid = ctx["tenant_id"]
    link_id = ctx["link_id"]
    cx_rt = str(ctx["channex_room_type_id"])
    cx_rp = str(ctx["channex_rate_plan_id"])
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    ci = datetime.now(UTC).date() + timedelta(days=50)
    co = ci + timedelta(days=2)
    rev_id = str(uuid4())
    book_id = str(uuid4())
    flat = _revision_flat(
        revision_id=rev_id,
        booking_id=book_id,
        status="new",
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    with patch(
        "app.services.channex_booking_service.lock_and_validate_availability",
        side_effect=LedgerNotSeededError("availability ledger not seeded"),
    ):
        async with tenant_transaction_session(factory, tid) as session:
            link = await session.get(ChannexPropertyLink, link_id)
            assert link is not None
            out = await ingest_channex_booking(session, tid, link, flat)

    assert out.schedule_availability_push is False
    assert out.skip_idempotent is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            row = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == rev_id,
                ),
            )
    assert row is not None
    assert row.processing_status == "error"
    assert (row.error_message or "").startswith("ledger not seeded:")
