"""Channex booking revision ingestion into OpenPMS bookings."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bookings.booking import Booking
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.rates.availability_ledger import AvailabilityLedger
from app.db.rls_session import tenant_transaction_session
from app.services.booking_service import InvalidBookingContextError
from app.services.channex_booking_service import (
    ChannexIngestResult,
    ingest_channex_booking,
)
from app.tasks.channex_webhook_task import _run_channex_process_webhook


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


def _revision_flat(
    *,
    revision_id: str,
    booking_id: str,
    status: str,
    cx_room_type_id: str,
    cx_rate_plan_id: str,
    ci: date,
    co: date,
    amount: str = "300.00",
) -> dict[str, object]:
    return {
        "id": revision_id,
        "booking_id": booking_id,
        "status": status,
        "amount": amount,
        "currency": "USD",
        "arrival_date": ci.isoformat(),
        "departure_date": co.isoformat(),
        "customer": {
            "name": "Ann",
            "surname": "Bee",
            "mail": f"ann-{booking_id[:8]}@ingest.test",
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


async def _seed_ledger_nights(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    room_type_id: UUID,
    nights: list[date],
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            for d in nights:
                existing = await session.scalar(
                    select(AvailabilityLedger).where(
                        AvailabilityLedger.tenant_id == tenant_id,
                        AvailabilityLedger.room_type_id == room_type_id,
                        AvailabilityLedger.date == d,
                    ),
                )
                if existing is None:
                    session.add(
                        AvailabilityLedger(
                            tenant_id=tenant_id,
                            room_type_id=room_type_id,
                            date=d,
                            total_rooms=5,
                            booked_rooms=0,
                            blocked_rooms=0,
                        ),
                    )


@pytest.mark.asyncio
async def test_channex_new_creates_booking_and_ledger(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=40)
    co = ci + timedelta(days=3)
    nights = [ci + timedelta(days=i) for i in range(3)]
    await _seed_ledger_nights(factory, tid, rt_id, nights)

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

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat)
    assert out.skip_idempotent is False
    assert out.schedule_availability_push is True
    assert out.success is True

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            booking = await session.scalar(
                select(Booking).where(
                    Booking.tenant_id == tid,
                    Booking.external_booking_id == book_id,
                ),
            )
            assert booking is not None
            assert booking.status == "confirmed"
            assert booking.source.startswith("channex:")
            ldg = await session.scalar(
                select(AvailabilityLedger).where(
                    AvailabilityLedger.tenant_id == tid,
                    AvailabilityLedger.room_type_id == rt_id,
                    AvailabilityLedger.date == nights[0],
                ),
            )
            assert ldg is not None
            assert ldg.booked_rooms == 1


@pytest.mark.asyncio
async def test_channex_ingest_idempotent_same_revision(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=50)
    co = ci + timedelta(days=2)
    nights = [ci, ci + timedelta(days=1)]
    await _seed_ledger_nights(factory, tid, rt_id, nights)

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
        amount="200.00",
    )

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out1 = await ingest_channex_booking(session, tid, link, flat)
    assert out1.skip_idempotent is False
    assert out1.success is True

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out2 = await ingest_channex_booking(session, tid, link, flat)
    assert out2.skip_idempotent is True
    assert out2.success is True


@pytest.mark.asyncio
async def test_channex_modified_updates_stay(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=80)
    co = ci + timedelta(days=2)
    ext_booking = str(uuid4())
    co_n = ci + timedelta(days=4)
    all_nights = [ci + timedelta(days=n) for n in range((co_n - ci).days)]
    await _seed_ledger_nights(factory, tid, rt_id, all_nights)

    rev_new = str(uuid4())
    flat_new = _revision_flat(
        revision_id=rev_new,
        booking_id=ext_booking,
        status="new",
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
        amount="200.00",
    )
    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        await ingest_channex_booking(session, tid, link, flat_new)

    rev_mod = str(uuid4())
    flat_mod = _revision_flat(
        revision_id=rev_mod,
        booking_id=ext_booking,
        status="modified",
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co_n,
        amount="330.00",
    )
    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat_mod)
    assert out.schedule_availability_push is True
    assert out.success is True

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            booking = await session.scalar(
                select(Booking).where(
                    Booking.tenant_id == tid,
                    Booking.external_booking_id == ext_booking,
                ),
            )
            assert booking is not None
            assert booking.total_amount == Decimal("330.00")


@pytest.mark.asyncio
async def test_channex_cancelled_frees_ledger(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=90)
    co = ci + timedelta(days=2)
    nights = [ci, ci + timedelta(days=1)]
    ext_booking = str(uuid4())
    await _seed_ledger_nights(factory, tid, rt_id, nights)

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        await ingest_channex_booking(
            session,
            tid,
            link,
            _revision_flat(
                revision_id=str(uuid4()),
                booking_id=ext_booking,
                status="new",
                cx_room_type_id=cx_rt,
                cx_rate_plan_id=cx_rp,
                ci=ci,
                co=co,
            ),
        )

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        await ingest_channex_booking(
            session,
            tid,
            link,
            _revision_flat(
                revision_id=str(uuid4()),
                booking_id=ext_booking,
                status="cancelled",
                cx_room_type_id=cx_rt,
                cx_rate_plan_id=cx_rp,
                ci=ci,
                co=co,
                amount="0",
            ),
        )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            booking = await session.scalar(
                select(Booking).where(
                    Booking.tenant_id == tid,
                    Booking.external_booking_id == ext_booking,
                ),
            )
            assert booking is not None
            assert booking.status == "cancelled"
            ldg = await session.scalar(
                select(AvailabilityLedger).where(
                    AvailabilityLedger.tenant_id == tid,
                    AvailabilityLedger.room_type_id == rt_id,
                    AvailabilityLedger.date == nights[0],
                ),
            )
            assert ldg is not None
            assert ldg.booked_rooms == 0


async def _fill_ledger_completely_booked(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    room_type_id: UUID,
    nights: list[date],
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            for d in nights:
                row = await session.scalar(
                    select(AvailabilityLedger).where(
                        AvailabilityLedger.tenant_id == tenant_id,
                        AvailabilityLedger.room_type_id == room_type_id,
                        AvailabilityLedger.date == d,
                    ),
                )
                assert row is not None
                row.booked_rooms = row.total_rooms


@pytest.mark.asyncio
async def test_channex_new_overbooking_marks_error(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    """Seq 250: no free rooms on stay nights → InsufficientInventoryError → revision error, no booking."""
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=100)
    co = ci + timedelta(days=3)
    nights = [ci + timedelta(days=i) for i in range(3)]
    await _seed_ledger_nights(factory, tid, rt_id, nights)
    await _fill_ledger_completely_booked(factory, tid, rt_id, nights)

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

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat)

    assert out.schedule_availability_push is False
    assert out.success is False
    assert out.skip_idempotent is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            rev = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == rev_id,
                ),
            )
            assert rev is not None
            assert rev.processing_status == "error"
            assert (rev.error_message or "").startswith("overbooking:")
            n_bookings = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Booking)
                        .where(
                            Booking.tenant_id == tid,
                            Booking.external_booking_id == book_id,
                        ),
                    )
                )
                or 0,
            )
            assert n_bookings == 0
            ldg0 = await session.scalar(
                select(AvailabilityLedger).where(
                    AvailabilityLedger.tenant_id == tid,
                    AvailabilityLedger.room_type_id == rt_id,
                    AvailabilityLedger.date == nights[0],
                ),
            )
            assert ldg0 is not None
            assert ldg0.booked_rooms == ldg0.total_rooms


@pytest.mark.asyncio
async def test_channex_new_ledger_not_seeded_marks_error(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    """Seq 251: no availability_ledger rows for stay dates → LedgerNotSeededError → revision error."""
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])

    ci = datetime.now(UTC).date() + timedelta(days=200)
    co = ci + timedelta(days=3)

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

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat)

    assert out.schedule_availability_push is False
    assert out.success is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            rev = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == rev_id,
                ),
            )
            assert rev is not None
            assert rev.processing_status == "error"
            assert (rev.error_message or "").startswith("ledger not seeded:")


@pytest.mark.asyncio
async def test_channex_guest_collision_marks_revision_error(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    """Seq 252: InvalidBookingContextError during guest create → revision error.

    Notion describes duplicate fallback email from matching external_id[:8]; the service
    uses the full booking_id in channex-{id}@guests.openpms.invalid, so two different ids
    do not collide. This test still covers the except-branch via a deterministic raise, as in
    test_ingest_new_guest_collision_marks_revision_error.
    """
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=110)
    co = ci + timedelta(days=2)
    nights = [ci, ci + timedelta(days=1)]
    await _seed_ledger_nights(factory, tid, rt_id, nights)

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

    async def _raise_guest_collision(*args: object, **kwargs: object) -> None:
        raise InvalidBookingContextError(
            "guest with this email already exists for this tenant",
        )

    async def _fake_lock(*args: object, **kwargs: object) -> list[MagicMock]:
        row = MagicMock()
        row.booked_rooms = 0
        return [row]

    with (
        patch(
            "app.services.channex_booking_service.lock_and_validate_availability",
            side_effect=_fake_lock,
        ),
        patch(
            "app.services.booking_service._get_or_create_guest_for_booking",
            side_effect=_raise_guest_collision,
        ),
    ):
        async with tenant_transaction_session(factory, tid) as session:
            link = await session.get(ChannexPropertyLink, link_id)
            assert link is not None
            out = await ingest_channex_booking(session, tid, link, flat)

    assert out.schedule_availability_push is False
    assert out.success is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            rev = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == rev_id,
                ),
            )
            assert rev is not None
            assert rev.processing_status == "error"
            assert (rev.error_message or "").startswith("guest creation failed:")


@pytest.mark.asyncio
async def test_channex_invalid_date_marks_revision_error(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    """Seq 253: invalid room dates → _parse_iso_date returns None → revision error."""
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])

    revision_id = str(uuid4())
    book_id = str(uuid4())
    flat: dict[str, object] = {
        "id": revision_id,
        "booking_id": book_id,
        "status": "confirmed",
        "amount": "100.00",
        "rooms": [
            {
                "room_type_id": cx_rt,
                "rate_plan_id": cx_rp,
                "checkin_date": "2026-13-45",
                "checkout_date": "2026-08-10",
            },
        ],
        "customer": {
            "name": "A",
            "surname": "B",
            "mail": "a@example.com",
            "phone": "+1",
        },
    }

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat)

    assert out.success is False
    assert out.schedule_availability_push is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            rev = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == revision_id,
                ),
            )
            assert rev is not None
            assert rev.processing_status == "error"
            assert "Invalid or missing arrival/departure dates" in (
                rev.error_message or ""
            )


@pytest.mark.asyncio
async def test_channex_unknown_room_type_marks_revision_error(
    channex_active_ctx: dict[str, object],
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    link_id = channex_active_ctx["link_id"]  # type: ignore[assignment]
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=60)
    co = ci + timedelta(days=2)
    await _seed_ledger_nights(factory, tid, rt_id, [ci, ci + timedelta(days=1)])

    flat = _revision_flat(
        revision_id=str(uuid4()),
        booking_id=str(uuid4()),
        status="new",
        cx_room_type_id=str(uuid4()),
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
    )

    async with tenant_transaction_session(factory, tid) as session:
        link = await session.get(ChannexPropertyLink, link_id)
        assert link is not None
        out = await ingest_channex_booking(session, tid, link, flat)
    assert out.schedule_availability_push is False
    assert out.success is False

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            rev = await session.scalar(
                select(ChannexBookingRevision).where(
                    ChannexBookingRevision.channex_revision_id == str(flat["id"]),
                ),
            )
            assert rev is not None
            assert rev.processing_status == "error"
            assert rev.error_message is not None


@pytest.mark.asyncio
async def test_channex_webhook_acknowledges_after_ingest(
    channex_active_ctx: dict[str, object],
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    cx_prop = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]
    cx_rt = str(channex_active_ctx["channex_room_type_id"])
    cx_rp = str(channex_active_ctx["channex_rate_plan_id"])
    rt_id = channex_active_ctx["room_type_id"]  # type: ignore[assignment]

    ci = datetime.now(UTC).date() + timedelta(days=70)
    co = ci + timedelta(days=2)
    await _seed_ledger_nights(factory, tid, rt_id, [ci, ci + timedelta(days=1)])

    rev_id = str(uuid4())
    webhook_log_id = uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            from app.models.integrations.channex_webhook_log import ChannexWebhookLog

            session.add(
                ChannexWebhookLog(
                    id=webhook_log_id,
                    tenant_id=tid,
                    event_type="booking",
                    payload={
                        "event": "booking_new",
                        "property_id": cx_prop,
                        "payload": {"id": rev_id},
                    },
                    signature=None,
                    ip_address=None,
                    processed=False,
                ),
            )

    flat = _revision_flat(
        revision_id=rev_id,
        booking_id=str(uuid4()),
        status="new",
        cx_room_type_id=cx_rt,
        cx_rate_plan_id=cx_rp,
        ci=ci,
        co=co,
        amount="150.00",
    )

    mock_client = AsyncMock()
    mock_client.get_booking_revision_raw = AsyncMock(return_value=flat)
    mock_client.acknowledge_revision = AsyncMock(return_value={})

    with patch(
        "app.tasks.channex_webhook_task._client_for_link",
        return_value=mock_client,
    ):
        with patch(
            "app.tasks.channex_incremental_ari.push_channex_availability",
        ) as mock_push:
            await _run_channex_process_webhook(webhook_log_id)

    mock_client.get_booking_revision_raw.assert_awaited_once_with(rev_id)
    mock_client.acknowledge_revision.assert_awaited_once_with(rev_id)
    mock_push.delay.assert_called()


@pytest.mark.asyncio
async def test_channex_webhook_skips_ack_when_ingest_not_successful(
    channex_active_ctx: dict[str, object],
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tid = channex_active_ctx["tenant_id"]  # type: ignore[assignment]
    cx_prop = channex_active_ctx["cx_property_id"]  # type: ignore[assignment]
    prop_id = channex_active_ctx["property_id"]  # type: ignore[assignment]

    rev_id = str(uuid4())
    webhook_log_id = uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            from app.models.integrations.channex_webhook_log import ChannexWebhookLog

            session.add(
                ChannexWebhookLog(
                    id=webhook_log_id,
                    tenant_id=tid,
                    event_type="booking",
                    payload={
                        "event": "booking_new",
                        "property_id": cx_prop,
                        "payload": {"id": rev_id},
                    },
                    signature=None,
                    ip_address=None,
                    processed=False,
                ),
            )

    mock_client = AsyncMock()
    mock_client.get_booking_revision_raw = AsyncMock(return_value={"id": rev_id})
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
        "app.tasks.channex_webhook_task._client_for_link",
        return_value=mock_client,
    ):
        with patch(
            "app.tasks.channex_webhook_task.ingest_channex_booking",
            new_callable=AsyncMock,
            return_value=ingest_result,
        ):
            await _run_channex_process_webhook(webhook_log_id)

    mock_client.get_booking_revision_raw.assert_awaited_once_with(rev_id)
    mock_client.acknowledge_revision.assert_not_called()
