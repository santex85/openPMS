"""Coverage for app.services.folio_service."""

from __future__ import annotations

import os
from datetime import date, time
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan
from app.schemas.folio import FolioPostRequest
from app.services.folio_service import (
    COUNTRY_PACK_TAX_PREFIX,
    FolioError,
    add_folio_entry,
    list_unpaid_folio_summary_for_property,
    replace_country_pack_tax_charges,
)

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_property_with_two_bookings(db_engine: object) -> dict[str, object]:
    tenant_id = uuid4()
    g1 = uuid4()
    g2 = uuid4()
    b1 = uuid4()
    b2 = uuid4()
    stay = date(2028, 1, 10)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

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
                    name="FolioCov",
                    billing_email="fc@example.com",
                    status="active",
                ),
            )
            await session.flush()
            prop = Property(
                tenant_id=tenant_id,
                name="Folio Prop",
                timezone="UTC",
                currency="THB",
                country_pack_code="TH",
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
                    name="301",
                    status="available",
                ),
            )
            session.add(
                Rate(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    rate_plan_id=rp.id,
                    date=stay,
                    price=Decimal("100.00"),
                ),
            )
            session.add(
                AvailabilityLedger(
                    tenant_id=tenant_id,
                    room_type_id=rt.id,
                    date=stay,
                    total_rooms=5,
                    booked_rooms=2,
                    blocked_rooms=0,
                ),
            )
            for gid, fn in ((g1, "Unpaid"), (g2, "Settled")):
                session.add(
                    Guest(
                        id=gid,
                        tenant_id=tenant_id,
                        first_name=fn,
                        last_name="Guest",
                        email=f"{fn.lower()}-{gid}@fc.example.com",
                        phone="+10000000004",
                    ),
                )
            await session.flush()
            for bid, gid in ((b1, g1), (b2, g2)):
                session.add(
                    Booking(
                        id=bid,
                        tenant_id=tenant_id,
                        property_id=prop.id,
                        guest_id=gid,
                        rate_plan_id=rp.id,
                        status="confirmed",
                        source="direct",
                        total_amount=Decimal("100.00"),
                    ),
                )
                session.add(
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=bid,
                        date=stay,
                        room_type_id=rt.id,
                        room_id=None,
                        price_for_date=Decimal("100.00"),
                    ),
                )
            # Unpaid: charge only
            session.add(
                FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=b1,
                    transaction_type="Charge",
                    amount=Decimal("80.00"),
                    payment_method=None,
                    description="Room",
                    created_by=None,
                    category="room_charge",
                ),
            )
            # Settled: charge + payment
            session.add(
                FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=b2,
                    transaction_type="Charge",
                    amount=Decimal("80.00"),
                    payment_method=None,
                    description="Room",
                    created_by=None,
                    category="room_charge",
                ),
            )
            session.add(
                FolioTransaction(
                    tenant_id=tenant_id,
                    booking_id=b2,
                    transaction_type="Payment",
                    amount=Decimal("80.00"),
                    payment_method="card",
                    description=None,
                    created_by=None,
                    category="payment",
                ),
            )
            pid = prop.id

    return {
        "tenant_id": tenant_id,
        "property_id": pid,
        "booking_unpaid": b1,
        "booking_settled": b2,
    }


@pytest.mark.asyncio
async def test_list_unpaid_folio_summary(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_property_with_two_bookings(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            rows = await list_unpaid_folio_summary_for_property(
                session,
                ctx["tenant_id"],
                ctx["property_id"],
            )
    assert len(rows) == 1
    assert rows[0][0] == ctx["booking_unpaid"]
    assert rows[0][1] == Decimal("80.00")
    assert rows[0][2] == "Unpaid"


@pytest.mark.asyncio
async def test_add_folio_entry_charge_discount(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_property_with_two_bookings(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            body = FolioPostRequest(
                entry_type="charge",
                amount=Decimal("15.00"),
                category="discount",
                description=" Promo ",
            )
            tx = await add_folio_entry(
                session,
                ctx["tenant_id"],
                ctx["booking_unpaid"],
                body,
                created_by=None,
            )
            assert tx.amount == Decimal("-15.00")


@pytest.mark.asyncio
async def test_add_folio_entry_payment_with_method(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_property_with_two_bookings(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            body = FolioPostRequest(
                entry_type="payment",
                amount=Decimal("10.00"),
                category="payment",
                payment_method=" cash ",
            )
            tx = await add_folio_entry(
                session,
                ctx["tenant_id"],
                ctx["booking_unpaid"],
                body,
                created_by=None,
            )
            assert tx.payment_method == "cash"


@pytest.mark.asyncio
async def test_add_folio_entry_invalid_category_422(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_property_with_two_bookings(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            body = FolioPostRequest.model_construct(
                entry_type="charge",
                amount=Decimal("10.00"),
                category="bogus",
                description=None,
                payment_method=None,
            )
            with pytest.raises(FolioError) as ei:
                await add_folio_entry(
                    session,
                    ctx["tenant_id"],
                    ctx["booking_unpaid"],
                    body,
                    created_by=None,
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_replace_country_pack_tax_full_path(db_engine: object) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_property_with_two_bookings(db_engine)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            session.add(
                FolioTransaction(
                    tenant_id=ctx["tenant_id"],
                    booking_id=ctx["booking_unpaid"],
                    transaction_type="Charge",
                    amount=Decimal("5.00"),
                    payment_method=None,
                    description=f"{COUNTRY_PACK_TAX_PREFIX} OLD: VAT",
                    created_by=None,
                    category="tax",
                ),
            )

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(ctx["tenant_id"])},
            )
            await replace_country_pack_tax_charges(
                session,
                ctx["tenant_id"],
                ctx["booking_unpaid"],
                ctx["property_id"],
                Decimal("1000.00"),
            )

    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(ctx["tenant_id"])},
        )
        tax_rows = (
            await session.scalars(
                select(FolioTransaction).where(
                    FolioTransaction.tenant_id == ctx["tenant_id"],
                    FolioTransaction.booking_id == ctx["booking_unpaid"],
                    FolioTransaction.category == "tax",
                ),
            )
        ).all()
    assert not any("OLD" in (r.description or "") for r in tax_rows)
    assert len(tax_rows) >= 1
    assert all((r.description or "").startswith(COUNTRY_PACK_TAX_PREFIX) for r in tax_rows)
