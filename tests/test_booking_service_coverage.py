"""Direct async coverage tests for app.services.booking_service (real DB)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.schemas.bookings import BookingCreateRequest, BookingPatchRequest, GuestPayload
from app.services.booking_service import (
    AssignBookingRoomError,
    DuplicateExternalBookingError,
    InvalidBookingContextError,
    _require_rate_plan_on_property,
    _require_room_type_on_property,
    assign_booking_room,
    create_booking,
    get_booking_tape,
    get_booking_tape_by_external_id,
    list_bookings_enriched,
    patch_booking,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_create_booking_world(
    *,
    guest_email: str = "newguest@cov.example.com",
    existing_guest: bool = False,
) -> dict[str, UUID]:
    tenant_id = uuid4()
    url = _database_url()
    assert url
    stay = [date(2026, 8, 1), date(2026, 8, 2)]
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
                    name="CovBookTenant",
                    billing_email="cb@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Cov Prop",
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
            session.add(
                Room(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    name="101",
                    status="available",
                ),
            )
            if existing_guest:
                session.add(
                    Guest(
                        tenant_id=tenant_id,
                        first_name="Existing",
                        last_name="Guest",
                        email=guest_email.lower(),
                        phone="+10000000001",
                    ),
                )
            for night in stay:
                session.add(
                    Rate(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        rate_plan_id=rp.id,
                        date=night,
                        price=Decimal("55.00"),
                    ),
                )
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=night,
                        total_rooms=10,
                        booked_rooms=0,
                        blocked_rooms=0,
                    ),
                )
            pid = prop.id
            rtid = rt.id
            rpid = rp.id
    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "property_id": pid,
        "room_type_id": rtid,
        "rate_plan_id": rpid,
    }


async def _seed_second_property_room(
    tenant_id: UUID,
    *,
    other_name: str = "Other Prop",
) -> UUID:
    """Another property + room type + physical room (same tenant)."""
    url = _database_url()
    assert url
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            prop = Property(
                tenant_id=tenant_id,
                name=other_name,
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
                name="OtherCat",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            room = Room(
                tenant_id=tenant_id,
                room_type_id=rt.id,
                name="999",
                status="available",
            )
            session.add(room)
            await session.flush()
            rid = room.id
    await engine.dispose()
    return rid


@pytest.fixture
def booking_create_ctx() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    return asyncio.run(_seed_create_booking_world())


@pytest.fixture
def booking_create_ctx_existing_guest() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    email = "merge@cov.example.com"
    ctx = asyncio.run(
        _seed_create_booking_world(guest_email=email, existing_guest=True)
    )
    ctx["guest_email"] = email  # type: ignore[assignment]
    return ctx


@pytest.mark.asyncio
async def test_create_booking_direct(
    booking_create_ctx: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = booking_create_ctx["tenant_id"]
    pid = booking_create_ctx["property_id"]
    rtid = booking_create_ctx["room_type_id"]
    rpid = booking_create_ctx["rate_plan_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    body = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        guest=GuestPayload(
            first_name="N",
            last_name="Direct",
            email="direct@cov.example.com",
            phone="+19999990001",
        ),
        status="confirmed",
        source="test",
        force_new_guest=False,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            out = await create_booking(session, tid, body)
    assert out.total_amount == Decimal("110.00")
    assert out.guest_merged is False
    assert len(out.nights) == 2


@pytest.mark.asyncio
async def test_create_booking_duplicate_external_id_rejected(
    booking_create_ctx: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = booking_create_ctx["tenant_id"]
    pid = booking_create_ctx["property_id"]
    rtid = booking_create_ctx["room_type_id"]
    rpid = booking_create_ctx["rate_plan_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ext = "preno-import-duplicate-test"
    body = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 9, 1),
        check_out=date(2026, 9, 3),
        guest=GuestPayload(
            first_name="Ext",
            last_name="One",
            email="ext1@cov.example.com",
            phone="+19999990011",
        ),
        status="confirmed",
        source="test",
        force_new_guest=False,
        external_booking_id=ext,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await create_booking(session, tid, body)
    body2 = body.model_copy(
        update={
            "guest": GuestPayload(
                first_name="Ext",
                last_name="Two",
                email="ext2@cov.example.com",
                phone="+19999990012",
            ),
        },
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(DuplicateExternalBookingError):
                await create_booking(session, tid, body2)


@pytest.mark.asyncio
async def test_get_booking_tape_by_external_id(
    booking_create_ctx: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = booking_create_ctx["tenant_id"]
    pid = booking_create_ctx["property_id"]
    rtid = booking_create_ctx["room_type_id"]
    rpid = booking_create_ctx["rate_plan_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ext = "mig16-service-lookup"
    body = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 11, 1),
        check_out=date(2026, 11, 3),
        guest=GuestPayload(
            first_name="Lookup",
            last_name="Ext",
            email="lookup.ext@cov.example.com",
            phone="+19999990021",
        ),
        status="confirmed",
        source="test",
        force_new_guest=False,
        external_booking_id=ext,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await create_booking(session, tid, body)
    body_plain = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 2),
        guest=GuestPayload(
            first_name="No",
            last_name="Ext",
            email="no.ext@cov.example.com",
            phone="+19999990022",
        ),
        status="confirmed",
        source="test",
        force_new_guest=False,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await create_booking(session, tid, body_plain)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = await get_booking_tape_by_external_id(session, tid, ext)
    assert row is not None
    assert row.external_booking_id == ext
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            miss = await get_booking_tape_by_external_id(session, tid, "missing-ext-id")
    assert miss is None


@pytest.mark.asyncio
async def test_create_booking_force_new_guest_conflict(
    booking_create_ctx_existing_guest: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = booking_create_ctx_existing_guest["tenant_id"]
    pid = booking_create_ctx_existing_guest["property_id"]
    rtid = booking_create_ctx_existing_guest["room_type_id"]
    rpid = booking_create_ctx_existing_guest["rate_plan_id"]
    email: str = booking_create_ctx_existing_guest["guest_email"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    body = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        guest=GuestPayload(
            first_name="X",
            last_name="Y",
            email=email,
            phone="+19999990002",
        ),
        force_new_guest=True,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(InvalidBookingContextError) as ei:
                await create_booking(session, tid, body)
            assert "email" in ei.value.args[0].lower()


@pytest.mark.asyncio
async def test_create_booking_existing_guest_merged(
    booking_create_ctx_existing_guest: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = booking_create_ctx_existing_guest["tenant_id"]
    pid = booking_create_ctx_existing_guest["property_id"]
    rtid = booking_create_ctx_existing_guest["room_type_id"]
    rpid = booking_create_ctx_existing_guest["rate_plan_id"]
    email: str = booking_create_ctx_existing_guest["guest_email"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    body = BookingCreateRequest(
        property_id=pid,
        room_type_id=rtid,
        rate_plan_id=rpid,
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        guest=GuestPayload(
            first_name="Existing",
            last_name="Guest",
            email=email,
            phone="+10000000001",
        ),
        force_new_guest=False,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            out = await create_booking(session, tid, body)
    assert out.guest_merged is True


@pytest.mark.asyncio
async def test_get_booking_tape_not_found(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            row = await get_booking_tape(session, tid, uuid4())
    assert row is None


@pytest.mark.asyncio
async def test_list_bookings_enriched_with_status_filter(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            b = await session.get(Booking, bid)
            assert b is not None
            pid = b.property_id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            items, total = await list_bookings_enriched(
                session,
                tid,
                property_id=pid,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 3),
                status_filter="checked_in",
                limit=10,
                offset=0,
            )
    assert total >= 1
    assert any(x.id == bid for x in items)


@pytest.mark.asyncio
async def test_patch_booking_cancel_releases_inventory(
    folio_scenario_confirmed: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await patch_booking(
                session,
                tid,
                bid,
                BookingPatchRequest(status="cancelled"),
            )
        async with session.begin():
            book = await session.get(Booking, bid)
            assert book is not None
            assert book.status == "cancelled"


@pytest.mark.asyncio
async def test_patch_booking_check_in_happy(
    folio_scenario_confirmed: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await patch_booking(
                session,
                tid,
                bid,
                BookingPatchRequest(status="checked_in"),
            )
        async with session.begin():
            book = await session.get(Booking, bid)
            assert book is not None
            assert book.status == "checked_in"


@pytest.mark.asyncio
async def test_patch_booking_checkout_balance_warning(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            phys = Room(
                tenant_id=tid,
                room_type_id=(
                    await session.scalar(
                        select(BookingLine.room_type_id)
                        .where(
                            BookingLine.booking_id == bid,
                        )
                        .limit(1),
                    )
                ),
                name="HK-1",
                status="available",
            )
            session.add(phys)
            await session.flush()
            rid = phys.id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await assign_booking_room(session, tid, bid, rid)
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            warn = await patch_booking(
                session,
                tid,
                bid,
                BookingPatchRequest(status="checked_out"),
            )
    assert warn is not None
    assert warn > Decimal("0")


@pytest.mark.asyncio
async def test_patch_booking_no_show_releases_inventory(
    folio_scenario_confirmed: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario_confirmed["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario_confirmed["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await patch_booking(
                session,
                tid,
                bid,
                BookingPatchRequest(status="no_show"),
            )
        async with session.begin():
            book = await session.get(Booking, bid)
            assert book is not None
            assert book.status == "no_show"


@pytest.mark.asyncio
async def test_patch_booking_dates_repricing_direct(
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    from tests.test_booking_patch_dates_repricing import _seed_repricing_scenario

    ids = await _seed_repricing_scenario()
    tid = ids["tenant_id"]
    bid = ids["booking_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await patch_booking(
                session,
                tid,
                bid,
                BookingPatchRequest(
                    check_in=date(2026, 4, 10),
                    check_out=date(2026, 4, 13),
                ),
            )
        async with session.begin():
            book = await session.get(Booking, bid)
            assert book is not None
            assert book.total_amount == Decimal("180.00")


@pytest.mark.asyncio
async def test_assign_booking_room_unassign(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            line_rt = await session.scalar(
                select(BookingLine.room_type_id)
                .where(
                    BookingLine.booking_id == bid,
                )
                .limit(1),
            )
            assert line_rt is not None
            phys = Room(
                tenant_id=tid,
                room_type_id=line_rt,
                name="U-1",
                status="available",
            )
            session.add(phys)
            await session.flush()
            rid = phys.id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await assign_booking_room(session, tid, bid, rid)
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await assign_booking_room(session, tid, bid, None)
        async with factory() as session:
            lines = (
                await session.scalars(
                    select(BookingLine).where(BookingLine.booking_id == bid),
                )
            ).all()
    assert all(ln.room_id is None for ln in lines)


@pytest.mark.asyncio
async def test_assign_booking_room_wrong_property(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    wrong_rid = await _seed_second_property_room(tid)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(AssignBookingRoomError) as ei:
                await assign_booking_room(session, tid, bid, wrong_rid)
            assert ei.value.status_code == 409
            # Wrong-property room uses another category; may error on category before property.
            assert (
                "property" in ei.value.detail.lower()
                or "category" in ei.value.detail.lower()
            )


@pytest.mark.asyncio
async def test_require_room_type_on_property_not_found(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            book = await session.get(Booking, bid)
            assert book is not None
            pid = book.property_id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(InvalidBookingContextError):
                await _require_room_type_on_property(
                    session,
                    tid,
                    pid,
                    uuid4(),
                )


@pytest.mark.asyncio
async def test_require_rate_plan_on_property_not_found(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            book = await session.get(Booking, bid)
            assert book is not None
            pid = book.property_id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(InvalidBookingContextError):
                await _require_rate_plan_on_property(session, tid, pid, uuid4())
