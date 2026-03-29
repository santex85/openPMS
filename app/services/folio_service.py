"""Folio listing, balance, posting charges/payments, reversal."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.booking import Booking
from app.models.bookings.folio_transaction import FolioTransaction
from app.services.audit_service import record_audit
from app.schemas.folio import CHARGE_CATEGORIES, FolioPostRequest


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


async def compute_folio_balance(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> Decimal:
    charge_sum = func.coalesce(
        func.sum(
            case(
                (FolioTransaction.transaction_type == "Charge", FolioTransaction.amount),
                else_=0,
            ),
        ),
        0,
    )
    payment_sum = func.coalesce(
        func.sum(
            case(
                (FolioTransaction.transaction_type == "Payment", FolioTransaction.amount),
                else_=0,
            ),
        ),
        0,
    )
    stmt = (
        select(charge_sum - payment_sum)
        .where(
            FolioTransaction.tenant_id == tenant_id,
            FolioTransaction.booking_id == booking_id,
        )
    )
    raw = await session.scalar(stmt)
    if raw is None:
        return Decimal("0.00")
    return Decimal(str(raw)).quantize(Decimal("0.01"))


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
