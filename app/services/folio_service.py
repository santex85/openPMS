"""Folio listing, balance, posting charges/payments, reversal."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.services.audit_service import record_audit
from app.models.billing.tax_config import TaxMode
from app.models.core.country_pack import CountryPack
from app.schemas.folio import CHARGE_CATEGORIES, FolioPostRequest

COUNTRY_PACK_TAX_PREFIX = "[country-pack-tax]"


class FolioError(Exception):
    """Invalid folio operation."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def _require_booking(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Booking:
    booking = await session.scalar(
        select(Booking).where(
            Booking.tenant_id == tenant_id,
            Booking.id == booking_id,
        ),
    )
    if booking is None:
        raise FolioError("booking not found", status_code=404)
    return booking


async def sum_booking_charge_amounts(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Decimal:
    """Total posted charges (absolute); used as receipt / property-tax base amount."""
    stmt = select(func.coalesce(func.sum(FolioTransaction.amount), 0)).where(
        FolioTransaction.tenant_id == tenant_id,
        FolioTransaction.booking_id == booking_id,
        FolioTransaction.transaction_type == "Charge",
    )
    raw = await session.scalar(stmt)
    if raw is None:
        return Decimal("0.00")
    return Decimal(str(raw)).quantize(Decimal("0.01"))


async def compute_folio_balance(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Decimal:
    charge_sum = func.coalesce(
        func.sum(
            case(
                (
                    FolioTransaction.transaction_type == "Charge",
                    FolioTransaction.amount,
                ),
                else_=0,
            ),
        ),
        0,
    )
    payment_sum = func.coalesce(
        func.sum(
            case(
                (
                    FolioTransaction.transaction_type == "Payment",
                    FolioTransaction.amount,
                ),
                else_=0,
            ),
        ),
        0,
    )
    stmt = select(charge_sum - payment_sum).where(
        FolioTransaction.tenant_id == tenant_id,
        FolioTransaction.booking_id == booking_id,
    )
    raw = await session.scalar(stmt)
    if raw is None:
        return Decimal("0.00")
    return Decimal(str(raw)).quantize(Decimal("0.01"))


async def list_unpaid_folio_summary_for_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> list[tuple[UUID, Decimal, str, str]]:
    """
    Bookings under ``property_id`` whose folio balance (charges − payments) is strictly positive.

    Returns ``(booking_id, balance, guest_first_name, guest_last_name)`` tuples.
    """
    charge_sum = func.coalesce(
        func.sum(
            case(
                (
                    FolioTransaction.transaction_type == "Charge",
                    FolioTransaction.amount,
                ),
                else_=0,
            ),
        ),
        0,
    )
    payment_sum = func.coalesce(
        func.sum(
            case(
                (
                    FolioTransaction.transaction_type == "Payment",
                    FolioTransaction.amount,
                ),
                else_=0,
            ),
        ),
        0,
    )
    balance_expr = charge_sum - payment_sum

    bal_sq = (
        select(FolioTransaction.booking_id, balance_expr.label("balance"))
        .where(FolioTransaction.tenant_id == tenant_id)
        .group_by(FolioTransaction.booking_id)
        .having(balance_expr > 0)
    ).subquery()

    stmt = (
        select(Booking.id, bal_sq.c.balance, Guest.first_name, Guest.last_name)
        .select_from(Booking)
        .join(bal_sq, bal_sq.c.booking_id == Booking.id)
        .join(
            Guest,
            (Guest.tenant_id == Booking.tenant_id) & (Guest.id == Booking.guest_id),
        )
        .where(
            Booking.tenant_id == tenant_id,
            Booking.property_id == property_id,
        )
        .order_by(bal_sq.c.balance.desc())
    )
    result = await session.execute(stmt)
    out: list[tuple[UUID, Decimal, str, str]] = []
    for bid, bal, fn, ln in result.all():
        out.append(
            (
                bid,
                Decimal(str(bal)).quantize(Decimal("0.01")),
                str(fn),
                str(ln),
            ),
        )
    return out


async def list_folio_transactions(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> tuple[list[FolioTransaction], Decimal]:
    await _require_booking(session, tenant_id, booking_id)
    stmt = (
        select(FolioTransaction)
        .where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
        )
        .order_by(FolioTransaction.created_at.asc(), FolioTransaction.id.asc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    balance = await compute_folio_balance(session, tenant_id, booking_id)
    return rows, balance


async def add_folio_entry(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    body: FolioPostRequest,
    created_by: UUID | None,
) -> FolioTransaction:
    await _require_booking(session, tenant_id, booking_id)

    if body.entry_type == "charge":
        if body.category not in CHARGE_CATEGORIES:
            raise FolioError("invalid category for charge", status_code=422)
        if body.category == "discount":
            stored_amount = -abs(body.amount)
        else:
            stored_amount = body.amount
        tx_type = "Charge"
        payment_method = None
    else:
        stored_amount = body.amount
        tx_type = "Payment"
        payment_method = body.payment_method.strip() if body.payment_method else None

    tx = FolioTransaction(
        tenant_id=tenant_id,
        booking_id=booking_id,
        transaction_type=tx_type,
        amount=stored_amount.quantize(Decimal("0.01")),
        payment_method=payment_method,
        description=body.description.strip() if body.description else None,
        created_by=created_by,
        category=body.category,
    )
    session.add(tx)
    await session.flush()
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="folio.post",
        entity_type="folio_transaction",
        entity_id=tx.id,
        new_values={
            "booking_id": str(booking_id),
            "entry_type": body.entry_type,
            "amount": str(stored_amount),
            "category": body.category,
        },
    )
    return tx


async def reverse_folio_transaction(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    transaction_id: UUID,
    created_by: UUID | None,
) -> FolioTransaction:
    await _require_booking(session, tenant_id, booking_id)
    tx = await session.scalar(
        select(FolioTransaction).where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
            FolioTransaction.id == transaction_id,
        ),
    )
    if tx is None:
        raise FolioError("folio transaction not found", status_code=404)
    if tx.amount == 0:
        raise FolioError("cannot reverse zero-amount transaction", status_code=409)

    rev = FolioTransaction(
        tenant_id=tenant_id,
        booking_id=booking_id,
        transaction_type=tx.transaction_type,
        amount=(-tx.amount).quantize(Decimal("0.01")),
        payment_method=tx.payment_method,
        description=f"Reversal of {transaction_id}",
        created_by=created_by,
        category=tx.category,
    )
    session.add(rev)
    await session.flush()
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="folio.reverse",
        entity_type="folio_transaction",
        entity_id=rev.id,
        old_values={"reversed_transaction_id": str(transaction_id)},
        new_values={"amount": str(rev.amount)},
    )
    return rev


async def replace_country_pack_tax_charges(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
    property_id: UUID,
    base_room_charge: Decimal,
) -> Decimal:
    """
    Remove prior auto-generated country-pack tax lines and post new ones from the
    property's pack. No-op (after cleanup) when property has no country_pack_code.
    """
    from app.models.core.property import Property

    from app.services.tax_service import (
        calculate_country_pack_tax_posting,
        get_tax_config,
    )

    await _require_booking(session, tenant_id, booking_id)

    await session.execute(
        delete(FolioTransaction).where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
            FolioTransaction.transaction_type == "Charge",
            FolioTransaction.category == "tax",
            FolioTransaction.description.like(f"{COUNTRY_PACK_TAX_PREFIX}%"),
        ),
    )
    await session.flush()

    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None or not prop.country_pack_code:
        return base_room_charge.quantize(Decimal("0.01"))

    cfg = await get_tax_config(session, tenant_id, property_id)
    mode = cfg.tax_mode if cfg is not None else TaxMode.exclusive
    if mode == TaxMode.off:
        return base_room_charge.quantize(Decimal("0.01"))

    pack = await session.scalar(
        select(CountryPack).where(CountryPack.code == prop.country_pack_code.strip()),
    )
    if pack is None:
        return base_room_charge.quantize(Decimal("0.01"))

    posting = calculate_country_pack_tax_posting(
        base_room_charge,
        pack.taxes,
        applies_to_category="room_charge",
        mode=mode,
    )
    for line in posting.lines:
        session.add(
            FolioTransaction(
                tenant_id=tenant_id,
                booking_id=booking_id,
                transaction_type="Charge",
                amount=line.amount.quantize(Decimal("0.01")),
                payment_method=None,
                description=f"{COUNTRY_PACK_TAX_PREFIX} {line.code}: {line.name}",
                created_by=None,
                category="tax",
            ),
        )
    await session.flush()
    return posting.room_charge_amount
