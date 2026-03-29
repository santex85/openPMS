#!/usr/bin/env python3
"""
For each booking_line with room_id IS NULL, set room_id to the first physical
Room (by name) for the same tenant_id + room_type_id.

Optional: set SEED_DEMO_BOARD_BOOKING=1 to insert one demo booking (3 nights,
starting the 5th of the current calendar month) on the first property that
has physical rooms, then assign room_id. Idempotent via booking.source.

Usage (API container):

  docker compose exec -T api sh -c 'cd /app && PYTHONPATH=/app python scripts/assign_room_ids_to_booking_lines.py'

  docker compose exec -T api sh -c 'cd /app && SEED_DEMO_BOARD_BOOKING=1 PYTHONPATH=/app python scripts/assign_room_ids_to_booking_lines.py'
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.guest import Guest
from app.models.core.room import Room
from app.models.core.room_type import RoomType

_DEMO_SOURCE = "assign_script_demo"


async def _seed_demo_booking_if_requested(session: AsyncSession) -> None:
    if os.environ.get("SEED_DEMO_BOARD_BOOKING") != "1":
        return

    existing = await session.scalar(
        select(Booking.id).where(Booking.source == _DEMO_SOURCE).limit(1),
    )
    if existing is not None:
        print("seed: demo booking already present, skipping insert")
        return

    row = (
        await session.execute(
            select(Room, RoomType.property_id)
            .join(
                RoomType,
                (RoomType.tenant_id == Room.tenant_id)
                & (RoomType.id == Room.room_type_id),
            )
            .order_by(Room.tenant_id, Room.name.asc())
            .limit(1),
        )
    ).first()
    if row is None:
        print("seed: no rooms in database, skipping demo insert", file=sys.stderr)
        return

    room, property_id = row
    today = date.today()
    first = today.replace(day=1)
    check_in = first + timedelta(days=4)
    if check_in.month != first.month:
        check_in = first
    nights = [check_in + timedelta(days=i) for i in range(3)]

    guest = Guest(
        id=uuid4(),
        tenant_id=room.tenant_id,
        first_name="Demo",
        last_name="Board",
        email="board-demo@openpms.local",
        phone="+10000000000",
    )
    session.add(guest)
    await session.flush()

    booking = Booking(
        id=uuid4(),
        tenant_id=room.tenant_id,
        property_id=property_id,
        guest_id=guest.id,
        status="confirmed",
        source=_DEMO_SOURCE,
        total_amount=Decimal("300.00"),
    )
    session.add(booking)
    await session.flush()

    for night in nights:
        session.add(
            BookingLine(
                id=uuid4(),
                tenant_id=room.tenant_id,
                booking_id=booking.id,
                date=night,
                room_type_id=room.room_type_id,
                room_id=None,
                price_for_date=Decimal("100.00"),
            ),
        )

    await session.commit()
    print(
        f"seed: inserted demo booking {booking.id} "
        f"({nights[0].isoformat()} .. {nights[-1].isoformat()})",
    )


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Missing DATABASE_URL", file=sys.stderr)
        return 1

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            await _seed_demo_booking_if_requested(session)

            result = await session.execute(
                select(BookingLine).where(BookingLine.room_id.is_(None)),
            )
            lines = list(result.scalars().all())

            cache: dict[tuple[UUID, UUID], UUID | None] = {}
            updated = 0
            skipped_no_room = 0

            for line in lines:
                key = (line.tenant_id, line.room_type_id)
                if key not in cache:
                    r = await session.execute(
                        select(Room.id)
                        .where(
                            Room.tenant_id == line.tenant_id,
                            Room.room_type_id == line.room_type_id,
                        )
                        .order_by(Room.name.asc())
                        .limit(1),
                    )
                    cache[key] = r.scalar_one_or_none()

                room_id = cache[key]
                if room_id is None:
                    skipped_no_room += 1
                    continue

                line.room_id = room_id
                updated += 1

            await session.commit()
            print(
                f"assign_room_ids_to_booking_lines: updated={updated}, "
                f"skipped_no_room={skipped_no_room}, total_null_scanned={len(lines)}",
            )
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
