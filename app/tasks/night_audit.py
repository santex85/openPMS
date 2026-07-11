"""Celery tasks: property-local night audit (auto no-show + owner email)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.sentry import capture_task_exception
from app.db.rls_session import tenant_transaction_session
from app.db.session import create_async_engine_and_sessionmaker
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.core.property import Property
from app.schemas.bookings import BookingPatchRequest
from app.services.audit_service import record_audit
from app.services.booking_service import PatchBookingError, patch_booking
from app.services.email_service import send_night_audit_email
from app.services.folio_service import list_unpaid_folio_summary_for_property
from app.worker import celery_app

log = structlog.get_logger()

_ACTIVE_EXCLUDE = ("cancelled", "no_show")
_MONEY_Q = Decimal("0.01")


def _money_str(value: Decimal) -> str:
    return format(Decimal(str(value)).quantize(_MONEY_Q), "f")


def property_local_now(timezone_id: str, *, now: datetime | None = None) -> datetime:
    try:
        tz = ZoneInfo(timezone_id.strip())
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    ref = now if now is not None else datetime.now(tz)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=tz)
    return ref.astimezone(tz)


async def _lookup_active_properties(
    session_factory: object,
) -> list[tuple[UUID, UUID, str]]:
    async with session_factory() as session:  # type: ignore[operator]
        res = await session.execute(
            text(
                "SELECT tenant_id, property_id, timezone "
                "FROM lookup_all_active_properties_for_worker()",
            ),
        )
        rows = res.fetchall()
    out: list[tuple[UUID, UUID, str]] = []
    for row in rows:
        out.append((UUID(str(row[0])), UUID(str(row[1])), str(row[2])))
    return out


async def _night_audit_fanout_async(*, now: datetime | None = None) -> list[str]:
    settings = get_settings()
    target_hour = int(settings.night_audit_hour)
    engine, factory = create_async_engine_and_sessionmaker(settings)
    enqueued: list[str] = []
    try:
        properties = await _lookup_active_properties(factory)
        for tenant_id, property_id, timezone_id in properties:
            local = property_local_now(timezone_id, now=now)
            if local.hour != target_hour:
                continue
            night_audit_property.delay(str(tenant_id), str(property_id))
            enqueued.append(str(property_id))
            log.info(
                "night_audit_enqueued",
                tenant_id=str(tenant_id),
                property_id=str(property_id),
                local_hour=local.hour,
            )
    finally:
        await engine.dispose()
    return enqueued


async def _count_arrivals(
    session: object,
    tenant_id: UUID,
    property_id: UUID,
    audit_date: object,
) -> int:
    min_date_subq = (
        select(func.min(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    return int(
        await session.scalar(  # type: ignore[attr-defined]
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.property_id == property_id,
                Booking.status.notin_(_ACTIVE_EXCLUDE),
                min_date_subq == audit_date,
            ),
        )
        or 0,
    )


async def _count_departures(
    session: object,
    tenant_id: UUID,
    property_id: UUID,
    audit_date: object,
) -> int:
    """Departures on audit_date: last night was the day before (dashboard convention)."""
    from datetime import date as date_cls

    assert isinstance(audit_date, date_cls)
    last_night = audit_date - timedelta(days=1)
    max_date_subq = (
        select(func.max(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    return int(
        await session.scalar(  # type: ignore[attr-defined]
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.property_id == property_id,
                Booking.status.notin_(_ACTIVE_EXCLUDE),
                max_date_subq == last_night,
            ),
        )
        or 0,
    )


async def _count_no_shows(
    session: object,
    tenant_id: UUID,
    property_id: UUID,
    audit_date: object,
) -> int:
    min_date_subq = (
        select(func.min(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    return int(
        await session.scalar(  # type: ignore[attr-defined]
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.property_id == property_id,
                Booking.status == "no_show",
                min_date_subq == audit_date,
            ),
        )
        or 0,
    )


async def _payments_total_for_local_day(
    session: object,
    tenant_id: UUID,
    property_id: UUID,
    timezone_id: str,
    audit_date: object,
) -> Decimal:
    local_date = func.date(func.timezone(timezone_id, FolioTransaction.created_at))
    raw = await session.scalar(  # type: ignore[attr-defined]
        select(func.coalesce(func.sum(FolioTransaction.amount), 0))
        .select_from(FolioTransaction)
        .join(
            Booking,
            (Booking.tenant_id == FolioTransaction.tenant_id)
            & (Booking.id == FolioTransaction.booking_id),
        )
        .where(
            FolioTransaction.tenant_id == tenant_id,
            Booking.property_id == property_id,
            FolioTransaction.transaction_type == "Payment",
            local_date == audit_date,
        ),
    )
    return Decimal(str(raw or 0)).quantize(_MONEY_Q)


async def _auto_no_show_confirmed_past_checkin(
    session: object,
    tenant_id: UUID,
    property_id: UUID,
    today_local: object,
) -> int:
    """Mark confirmed bookings whose first night is before today as no_show."""
    min_date_subq = (
        select(func.min(BookingLine.date))
        .where(
            BookingLine.tenant_id == Booking.tenant_id,
            BookingLine.booking_id == Booking.id,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    result = await session.execute(  # type: ignore[attr-defined]
        select(Booking.id).where(
            Booking.tenant_id == tenant_id,
            Booking.property_id == property_id,
            Booking.status == "confirmed",
            min_date_subq < today_local,
        ),
    )
    booking_ids = [row[0] for row in result.all()]
    marked = 0
    for bid in booking_ids:
        try:
            await patch_booking(
                session,  # type: ignore[arg-type]
                tenant_id,
                bid,
                BookingPatchRequest(status="no_show"),
            )
            await record_audit(
                session,  # type: ignore[arg-type]
                tenant_id=tenant_id,
                action="booking.night_audit_no_show",
                entity_type="booking",
                entity_id=bid,
                old_values={"status": "confirmed"},
                new_values={"status": "no_show"},
            )
            marked += 1
        except PatchBookingError as exc:
            log.warning(
                "night_audit_no_show_failed",
                booking_id=str(bid),
                error=str(exc),
            )
    return marked


async def _night_audit_property_async(
    tenant_id: UUID, property_id: UUID
) -> dict[str, object]:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            prop = await session.scalar(
                select(Property).where(
                    Property.tenant_id == tenant_id,
                    Property.id == property_id,
                ),
            )
            if prop is None:
                log.warning(
                    "night_audit_property_missing",
                    tenant_id=str(tenant_id),
                    property_id=str(property_id),
                )
                return {"ok": False, "reason": "property_not_found"}

            local_now = property_local_now(prop.timezone)
            today_local = local_now.date()
            yesterday_local = today_local - timedelta(days=1)
            day_start_local = datetime(
                today_local.year,
                today_local.month,
                today_local.day,
                tzinfo=local_now.tzinfo,
            )
            day_end_local = day_start_local + timedelta(days=1)
            day_start_utc = day_start_local.astimezone(ZoneInfo("UTC"))
            day_end_utc = day_end_local.astimezone(ZoneInfo("UTC"))

            auto_no_shows = await _auto_no_show_confirmed_past_checkin(
                session,
                tenant_id,
                property_id,
                today_local,
            )

            arrivals = await _count_arrivals(
                session, tenant_id, property_id, yesterday_local
            )
            departures = await _count_departures(
                session, tenant_id, property_id, yesterday_local
            )
            no_shows = await _count_no_shows(
                session, tenant_id, property_id, yesterday_local
            )
            payments = await _payments_total_for_local_day(
                session,
                tenant_id,
                property_id,
                prop.timezone.strip(),
                yesterday_local,
            )
            unpaid_raw = await list_unpaid_folio_summary_for_property(
                session,
                tenant_id,
                property_id,
                departed_on=yesterday_local,
                checked_out_only=True,
            )
            unpaid_folios = [
                {
                    "guest_name": f"{fn} {ln}".strip() or str(bid),
                    "balance": _money_str(bal),
                }
                for bid, bal, fn, ln in unpaid_raw
            ]

            emailed = await send_night_audit_email(
                session,
                tenant_id,
                prop,
                audit_date=yesterday_local,
                arrivals=arrivals,
                departures=departures,
                no_shows=no_shows,
                auto_no_shows=auto_no_shows,
                payments_total=_money_str(payments),
                unpaid_folios=unpaid_folios,
                day_start_utc=day_start_utc,
                day_end_utc=day_end_utc,
            )
            return {
                "ok": True,
                "auto_no_shows": auto_no_shows,
                "arrivals": arrivals,
                "departures": departures,
                "no_shows": no_shows,
                "emailed": emailed,
                "audit_date": yesterday_local.isoformat(),
            }
    finally:
        await engine.dispose()


@celery_app.task(name="night_audit_fanout")
def night_audit_fanout() -> list[str]:
    return asyncio.run(_night_audit_fanout_async())


@celery_app.task(name="night_audit_property")
def night_audit_property(tenant_id: str, property_id: str) -> dict[str, object]:
    tid = UUID(tenant_id)
    pid = UUID(property_id)
    try:
        return asyncio.run(_night_audit_property_async(tid, pid))
    except Exception as exc:
        log.exception(
            "night_audit_property_failed",
            tenant_id=tenant_id,
            property_id=property_id,
        )
        capture_task_exception(
            exc,
            task_name="night_audit_property",
            tenant_id=tenant_id,
        )
        raise
