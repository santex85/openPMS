"""Direct tests for folio_service edge cases."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bookings.folio_transaction import FolioTransaction
from app.services.folio_service import (
    FolioError,
    replace_country_pack_tax_charges,
    reverse_folio_transaction,
)

from tests.db_seed import disable_row_security_for_test_seed
from tests.test_channex_webhook_sync import _database_url


@pytest.mark.asyncio
async def test_reverse_folio_rejects_zero_amount_tx(
    folio_scenario: dict,
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid = folio_scenario["tenant_id"]
    bid = folio_scenario["booking_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    zid = uuid4()

    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            session.add(
                FolioTransaction(
                    id=zid,
                    tenant_id=tid,
                    booking_id=bid,
                    transaction_type="Charge",
                    amount=Decimal("0.00"),
                    payment_method=None,
                    description="zero",
                    created_by=None,
                    category="food_beverage",
                ),
            )

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            with pytest.raises(FolioError) as ei:
                await reverse_folio_transaction(session, tid, bid, zid, created_by=None)
            assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_replace_country_pack_tax_no_pack_code_noop(
    folio_scenario: dict,
    db_engine: object,
) -> None:
    if not _database_url():
        pytest.skip("DATABASE_URL required")
    tid = folio_scenario["tenant_id"]
    bid = folio_scenario["booking_id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    from sqlalchemy import select

    from app.models.bookings.booking import Booking

    async with factory() as session:
        async with session.begin():
            book = await session.get(Booking, bid)
            assert book is not None
            pid = book.property_id
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tid)},
            )
            await replace_country_pack_tax_charges(
                session,
                tid,
                bid,
                pid,
                Decimal("100.00"),
            )
        async with session.begin():
            tax_rows = (
                await session.scalars(
                    select(FolioTransaction).where(
                        FolioTransaction.booking_id == bid,
                        FolioTransaction.category == "tax",
                    ),
                )
            ).all()
    assert tax_rows == []
