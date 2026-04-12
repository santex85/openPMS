"""Direct unit tests for app.services.booking_service helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.core.room import Room
from app.services.booking_service import (
    AssignBookingRoomError,
    _mark_assigned_rooms_dirty_on_checkout,
    _pick_first_free_room_for_stay,
    _update_folio_charge_amount,
    assign_booking_room,
)


@pytest.mark.asyncio
async def test_assign_booking_room_raises_when_cancelled(
    room_conflict_scenario: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = room_conflict_scenario["tenant_id"]
    ba = room_conflict_scenario["booking_a"]
    rid = room_conflict_scenario["room_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            book = await session.get(Booking, ba)
            assert book is not None
            book.status = "cancelled"

    async with factory() as session:
        async with session.begin():
            with pytest.raises(AssignBookingRoomError) as ei:
                await assign_booking_room(session, tid, ba, rid)
            assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_assign_booking_room_rejects_wrong_room_type(
    room_conflict_scenario: dict[str, UUID],
    db_engine: object,
) -> None:
    """Physical room whose category does not match booking lines' room type."""
    tid = room_conflict_scenario["tenant_id"]
    ba = room_conflict_scenario["booking_a"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    from app.models.core.room import Room
    from app.models.core.room_type import RoomType

    async with factory() as session:
        async with session.begin():
            book = await session.get(Booking, ba)
            assert book is not None
            rt_suite = RoomType(
                tenant_id=tid,
                property_id=book.property_id,
                name="SuiteOther",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt_suite)
            await session.flush()
            wrong_cat_room = Room(
                tenant_id=tid,
                room_type_id=rt_suite.id,
                name="999",
                status="available",
            )
            session.add(wrong_cat_room)
            await session.flush()
            bad_rid = wrong_cat_room.id

    async with factory() as session:
        async with session.begin():
            with pytest.raises(AssignBookingRoomError) as ei:
                await assign_booking_room(session, tid, ba, bad_rid)
            assert ei.value.status_code == 409
            assert "category" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_pick_first_free_room_none_when_only_room_taken(
    room_conflict_scenario: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = room_conflict_scenario["tenant_id"]
    ba = room_conflict_scenario["booking_a"]
    bb = room_conflict_scenario["booking_b"]
    rid = room_conflict_scenario["room_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await assign_booking_room(session, tid, ba, rid)
        b = await session.get(Booking, bb)
        assert b is not None
        lines = (
            await session.scalars(
                select(BookingLine).where(BookingLine.booking_id == bb),
            )
        ).all()
        nights = sorted({ln.date for ln in lines})
        prop_id = b.property_id
        rt_id = lines[0].room_type_id

    async with factory() as session:
        async with session.begin():
            choice = await _pick_first_free_room_for_stay(
                session,
                tid,
                prop_id,
                rt_id,
                nights,
                bb,
            )
        assert choice is None


@pytest.mark.asyncio
async def test_update_folio_room_charge_amount(
    folio_scenario: dict[str, object],
    db_engine: object,
) -> None:
    tid: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    bid: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await _update_folio_charge_amount(session, tid, bid, Decimal("222.22"))
        async with session.begin():
            ft = await session.scalar(
                select(FolioTransaction)
                .where(
                    FolioTransaction.booking_id == bid,
                    FolioTransaction.category == "room_charge",
                )
                .order_by(FolioTransaction.created_at.asc())
                .limit(1),
            )
        assert ft is not None
        assert ft.amount == Decimal("222.22")


@pytest.mark.asyncio
async def test_mark_assigned_rooms_dirty_on_checkout(
    room_conflict_scenario: dict[str, UUID],
    db_engine: object,
) -> None:
    tid = room_conflict_scenario["tenant_id"]
    ba = room_conflict_scenario["booking_a"]
    rid = room_conflict_scenario["room_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await assign_booking_room(session, tid, ba, rid)
            room = await session.get(Room, rid)
            assert room is not None
            room.housekeeping_status = "clean"
        async with session.begin():
            await _mark_assigned_rooms_dirty_on_checkout(session, tid, ba)
        async with session.begin():
            room2 = await session.get(Room, rid)
        assert room2 is not None
        assert room2.housekeeping_status == "dirty"
