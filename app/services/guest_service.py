"""Guest CRUD and search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bookings.booking import Booking
from app.models.bookings.guest import Guest
from app.schemas.guest import GuestBookingSummary, GuestCreate, GuestPatch


class GuestServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def list_guests(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Guest], int]:
    filters = [Guest.tenant_id == tenant_id]
    if q is not None and q.strip():
        term = f"%{q.strip()}%"
        filters.append(
            or_(
                Guest.first_name.ilike(term),
                Guest.last_name.ilike(term),
                Guest.email.ilike(term),
                Guest.phone.ilike(term),
            ),
        )
    count_stmt = select(func.count()).select_from(Guest).where(*filters)
    total = int(await session.scalar(count_stmt) or 0)
    stmt = (
        select(Guest)
        .where(*filters)
        .order_by(Guest.updated_at.desc(), Guest.last_name.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def get_guest(
    session: AsyncSession,
    tenant_id: UUID,
    guest_id: UUID,
) -> Guest | None:
    return await session.scalar(
        select(Guest).where(
            Guest.tenant_id == tenant_id,
            Guest.id == guest_id,
        ),
    )


def _summarize_booking(
    booking: Booking,
    line_dates: list,
) -> GuestBookingSummary:
    if not line_dates:
        return GuestBookingSummary(
            id=booking.id,
            property_id=booking.property_id,
            status=booking.status,
            source=booking.source,
            total_amount=booking.total_amount,
            check_in_date=None,
            check_out_date=None,
        )
    check_in = min(line_dates)
    check_out = max(line_dates) + timedelta(days=1)
    return GuestBookingSummary(
        id=booking.id,
        property_id=booking.property_id,
        status=booking.status,
        source=booking.source,
        total_amount=booking.total_amount,
        check_in_date=check_in,
        check_out_date=check_out,
    )


async def get_guest_with_booking_summaries(
    session: AsyncSession,
    tenant_id: UUID,
    guest_id: UUID,
) -> tuple[Guest | None, list[GuestBookingSummary]]:
    guest = await get_guest(session, tenant_id, guest_id)
    if guest is None:
        return None, []

    b_stmt = (
        select(Booking)
        .options(selectinload(Booking.lines))
        .where(
            Booking.tenant_id == tenant_id,
            Booking.guest_id == guest_id,
        )
        .order_by(Booking.id.desc())
    )
    b_result = await session.execute(b_stmt)
    bookings = list(b_result.scalars().unique().all())
    if not bookings:
        return guest, []

    summaries = [
        _summarize_booking(b, [ln.date for ln in b.lines]) for b in bookings
    ]
    return guest, summaries


async def create_guest(
    session: AsyncSession,
    tenant_id: UUID,
    body: GuestCreate,
) -> Guest:
    email_norm = body.email.strip().lower()
    row = Guest(
        tenant_id=tenant_id,
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        email=email_norm,
        phone=body.phone.strip(),
        passport_data=body.passport_data.strip() if body.passport_data else None,
        nationality=body.nationality,
        date_of_birth=body.date_of_birth,
        notes=body.notes.strip() if body.notes else None,
        vip_status=body.vip_status,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise GuestServiceError(
            "guest with this email already exists",
            status_code=409,
        ) from exc
    return row


async def patch_guest(
    session: AsyncSession,
    tenant_id: UUID,
    guest_id: UUID,
    body: GuestPatch,
) -> Guest:
    row = await get_guest(session, tenant_id, guest_id)
    if row is None:
        raise GuestServiceError("guest not found", status_code=404)

    data = body.model_dump(exclude_unset=True)
    if "first_name" in data:
        row.first_name = data["first_name"].strip()
    if "last_name" in data:
        row.last_name = data["last_name"].strip()
    if "email" in data:
        row.email = data["email"].strip().lower()
    if "phone" in data:
        row.phone = data["phone"].strip()
    if "passport_data" in data:
        v = data["passport_data"]
        row.passport_data = v.strip() if v else None
    if "nationality" in data:
        row.nationality = data["nationality"]
    if "date_of_birth" in data:
        row.date_of_birth = data["date_of_birth"]
    if "notes" in data:
        v = data["notes"]
        row.notes = v.strip() if v else None
    if "vip_status" in data and data["vip_status"] is not None:
        row.vip_status = bool(data["vip_status"])

    row.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise GuestServiceError(
            "guest with this email already exists",
            status_code=409,
        ) from exc
    return row
