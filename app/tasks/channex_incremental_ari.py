"""Incremental Channex ARI pushes (availability / restrictions) for date subsets."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.rls_session import tenant_transaction_session
from app.db.session import create_async_engine_and_sessionmaker
from app.integrations.channex.client import ChannexApiError, ChannexClient
from app.integrations.channex.rate_value import channex_rate_string
from app.models.core.property import Property
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.services.channex_service import _client_for_link, _get_link
from app.worker import celery_app

log = structlog.get_logger()

_BATCH = 200
_BATCH_DELAY_SEC = 0.5


async def _sleep_between_batches() -> None:
    await asyncio.sleep(_BATCH_DELAY_SEC)


async def _property_currency_code(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> str:
    raw = await session.scalar(
        select(Property.currency).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    cc = (raw or "USD").strip().upper()[:3]
    if len(cc) != 3:
        return "USD"
    return cc


def _restriction_payload(
    prop_cx: str,
    cx_rp: str,
    d: date,
    currency_code: str,
    *,
    price: Decimal | None,
    stop_sell: bool | None = None,
    min_stay_arrival: int | None = None,
    max_stay: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "property_id": prop_cx,
        "rate_plan_id": cx_rp,
        "date": d.isoformat(),
    }
    if price is not None:
        row["rate"] = channex_rate_string(price, currency_code)
    if stop_sell is not None:
        row["stop_sell"] = stop_sell
    if min_stay_arrival is not None:
        row["min_stay_arrival"] = min_stay_arrival
    if max_stay is not None:
        row["max_stay"] = max_stay
    return row


async def _run_push_channex_availability(
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    date_strs: list[str],
) -> None:
    if not date_strs:
        return
    dates = sorted({date.fromisoformat(s) for s in date_strs})
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            link_row = await _get_link(session, tenant_id, property_id)
            if link_row is None or link_row.status != "active":
                log.info("channex_incremental_avail_skip_no_link")
                return

            stmt = select(ChannexRoomTypeMap).where(
                ChannexRoomTypeMap.tenant_id == tenant_id,
                ChannexRoomTypeMap.property_link_id == link_row.id,
                ChannexRoomTypeMap.room_type_id == room_type_id,
            )
            rm = (await session.execute(stmt)).scalar_one_or_none()
            if rm is None:
                log.warning("channex_incremental_avail_no_room_map")
                return

            stmt_al = select(AvailabilityLedger).where(
                AvailabilityLedger.tenant_id == tenant_id,
                AvailabilityLedger.room_type_id == room_type_id,
                AvailabilityLedger.date.in_(dates),
            )
            ledger_by_date: dict[date, AvailabilityLedger] = {}
            for row in (await session.execute(stmt_al)).scalars().all():
                ledger_by_date[row.date] = row

            prop_cx = link_row.channex_property_id.strip()
            cx_rt = rm.channex_room_type_id.strip()
            av_values: list[dict[str, object]] = []
            for d in dates:
                lr = ledger_by_date.get(d)
                avail = 0
                if lr is not None:
                    avail = lr.total_rooms - lr.booked_rooms - lr.blocked_rooms
                    avail = max(0, avail)
                av_values.append(
                    {
                        "property_id": prop_cx,
                        "room_type_id": cx_rt,
                        "date": d.isoformat(),
                        "availability": avail,
                    },
                )

            client: ChannexClient = _client_for_link(link_row)
            try:
                for i in range(0, len(av_values), _BATCH):
                    await client.push_availability(av_values[i : i + _BATCH])
                    await _sleep_between_batches()
                link_row.error_message = None  # type: ignore[attr-defined]
            except ChannexApiError as exc:
                msg = exc.args[0] if exc.args else "Channex API error"
                link_row.error_message = msg[:2000]  # type: ignore[attr-defined]
                await session.flush()
                log.exception("channex_incremental_avail_failed", error=msg)
                return
            await session.flush()
            log.info("channex_incremental_avail_ok", count=len(av_values))
    finally:
        await engine.dispose()


async def _run_push_channex_rates(
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    rate_plan_id: UUID,
    date_strs: list[str],
) -> None:
    if not date_strs:
        return
    dates = sorted({date.fromisoformat(s) for s in date_strs})
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            link_row = await _get_link(session, tenant_id, property_id)
            if link_row is None or link_row.status != "active":
                log.info("channex_incremental_rates_skip_no_link")
                return

            stmt_rm = select(ChannexRoomTypeMap).where(
                ChannexRoomTypeMap.tenant_id == tenant_id,
                ChannexRoomTypeMap.property_link_id == link_row.id,
                ChannexRoomTypeMap.room_type_id == room_type_id,
            )
            rtm = (await session.execute(stmt_rm)).scalar_one_or_none()
            if rtm is None:
                log.warning("channex_incremental_rates_no_room_map")
                return

            stmt_rpm = select(ChannexRatePlanMap).where(
                ChannexRatePlanMap.tenant_id == tenant_id,
                ChannexRatePlanMap.room_type_map_id == rtm.id,
                ChannexRatePlanMap.rate_plan_id == rate_plan_id,
            )
            rpm = (await session.execute(stmt_rpm)).scalar_one_or_none()
            if rpm is None:
                log.warning("channex_incremental_rates_no_rate_map")
                return

            currency_code = await _property_currency_code(
                session,
                tenant_id,
                link_row.property_id,
            )

            stmt_rates = select(Rate).where(
                Rate.tenant_id == tenant_id,
                Rate.room_type_id == room_type_id,
                Rate.rate_plan_id == rate_plan_id,
                Rate.date.in_(dates),
            )
            rate_rows = list((await session.execute(stmt_rates)).scalars().all())
            by_date = {r.date: r for r in rate_rows}

            prop_cx = link_row.channex_property_id.strip()
            cx_rp = rpm.channex_rate_plan_id.strip()
            rest_values: list[dict[str, object]] = []
            for d in dates:
                rrow = by_date.get(d)
                if rrow is None:
                    continue
                dec = rrow.price
                if dec <= 0:
                    continue
                payload = _restriction_payload(
                    prop_cx,
                    cx_rp,
                    d,
                    currency_code,
                    price=dec,
                    stop_sell=rrow.stop_sell,
                    min_stay_arrival=rrow.min_stay_arrival,
                    max_stay=rrow.max_stay,
                )
                rest_values.append(payload)

            if not rest_values:
                log.info("channex_incremental_rates_no_rows")
                return

            client: ChannexClient = _client_for_link(link_row)
            try:
                for i in range(0, len(rest_values), _BATCH):
                    await client.push_restrictions(rest_values[i : i + _BATCH])
                    await _sleep_between_batches()
                link_row.error_message = None  # type: ignore[attr-defined]
            except ChannexApiError as exc:
                msg = exc.args[0] if exc.args else "Channex API error"
                link_row.error_message = msg[:2000]  # type: ignore[attr-defined]
                await session.flush()
                log.exception("channex_incremental_rates_failed", error=msg)
                return
            await session.flush()
            log.info("channex_incremental_rates_ok", count=len(rest_values))
    finally:
        await engine.dispose()


async def _run_push_channex_stop_sell(
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    date_strs: list[str],
) -> None:
    if not date_strs:
        return
    dates = sorted({date.fromisoformat(s) for s in date_strs})
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            link_row = await _get_link(session, tenant_id, property_id)
            if link_row is None or link_row.status != "active":
                log.info("channex_incremental_stop_sell_skip_no_link")
                return

            stmt_rm = select(ChannexRoomTypeMap).where(
                ChannexRoomTypeMap.tenant_id == tenant_id,
                ChannexRoomTypeMap.property_link_id == link_row.id,
                ChannexRoomTypeMap.room_type_id == room_type_id,
            )
            rtm = (await session.execute(stmt_rm)).scalar_one_or_none()
            if rtm is None:
                log.warning("channex_incremental_stop_sell_no_room_map")
                return

            stmt_maps = select(ChannexRatePlanMap).where(
                ChannexRatePlanMap.tenant_id == tenant_id,
                ChannexRatePlanMap.room_type_map_id == rtm.id,
            )
            rate_maps = list((await session.execute(stmt_maps)).scalars().all())
            if not rate_maps:
                log.warning("channex_incremental_stop_sell_no_rate_maps")
                return

            currency_code = await _property_currency_code(
                session,
                tenant_id,
                link_row.property_id,
            )

            prop_cx = link_row.channex_property_id.strip()
            rp_ids = {m.rate_plan_id for m in rate_maps}
            stmt_rates = select(Rate).where(
                Rate.tenant_id == tenant_id,
                Rate.room_type_id == room_type_id,
                Rate.rate_plan_id.in_(rp_ids),
                Rate.date.in_(dates),
            )
            rate_lookup: dict[tuple[UUID, date], Rate] = {}
            for rrow in (await session.execute(stmt_rates)).scalars().all():
                rate_lookup[(rrow.rate_plan_id, rrow.date)] = rrow

            rest_values: list[dict[str, object]] = []
            for rpm in rate_maps:
                cx_rp = rpm.channex_rate_plan_id.strip()
                for d in dates:
                    rrow = rate_lookup.get((rpm.rate_plan_id, d))
                    price = (
                        rrow.price
                        if rrow is not None and rrow.price > Decimal("0")
                        else None
                    )
                    rest_values.append(
                        _restriction_payload(
                            prop_cx,
                            cx_rp,
                            d,
                            currency_code,
                            price=price,
                            stop_sell=True,
                        ),
                    )

            client: ChannexClient = _client_for_link(link_row)
            try:
                for i in range(0, len(rest_values), _BATCH):
                    await client.push_restrictions(rest_values[i : i + _BATCH])
                    await _sleep_between_batches()
                link_row.error_message = None  # type: ignore[attr-defined]
            except ChannexApiError as exc:
                msg = exc.args[0] if exc.args else "Channex API error"
                link_row.error_message = msg[:2000]  # type: ignore[attr-defined]
                await session.flush()
                log.exception("channex_incremental_stop_sell_failed", error=msg)
                return
            await session.flush()
            log.info("channex_incremental_stop_sell_ok", count=len(rest_values))
    finally:
        await engine.dispose()


def _retry_countdown(retries: int) -> int:
    return min(125, 5 * (5**retries))


@celery_app.task(
    bind=True,
    name="push_channex_availability",
    max_retries=3,
    default_retry_delay=5,
)
def push_channex_availability(
    self: object,
    tenant_id: str,
    property_id: str,
    room_type_id: str,
    date_strs: list[str],
) -> None:
    try:
        asyncio.run(
            _run_push_channex_availability(
                UUID(tenant_id),
                UUID(property_id),
                UUID(room_type_id),
                date_strs,
            ),
        )
    except Exception as exc:
        retries = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
        raise self.retry(exc=exc, countdown=_retry_countdown(retries)) from exc  # type: ignore[attr-defined]


@celery_app.task(
    bind=True,
    name="push_channex_rates",
    max_retries=3,
    default_retry_delay=5,
)
def push_channex_rates(
    self: object,
    tenant_id: str,
    property_id: str,
    room_type_id: str,
    rate_plan_id: str,
    date_strs: list[str],
) -> None:
    try:
        asyncio.run(
            _run_push_channex_rates(
                UUID(tenant_id),
                UUID(property_id),
                UUID(room_type_id),
                UUID(rate_plan_id),
                date_strs,
            ),
        )
    except Exception as exc:
        retries = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
        raise self.retry(exc=exc, countdown=_retry_countdown(retries)) from exc  # type: ignore[attr-defined]


@celery_app.task(
    bind=True,
    name="push_channex_stop_sell",
    max_retries=3,
    default_retry_delay=5,
)
def push_channex_stop_sell(
    self: object,
    tenant_id: str,
    property_id: str,
    room_type_id: str,
    date_strs: list[str],
) -> None:
    try:
        asyncio.run(
            _run_push_channex_stop_sell(
                UUID(tenant_id),
                UUID(property_id),
                UUID(room_type_id),
                date_strs,
            ),
        )
    except Exception as exc:
        retries = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
        raise self.retry(exc=exc, countdown=_retry_countdown(retries)) from exc  # type: ignore[attr-defined]
