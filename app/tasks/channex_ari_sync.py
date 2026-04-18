"""Celery task: push availability + rates to Channex for one OpenPMS property."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import create_async_engine_and_sessionmaker
from app.db.rls_session import tenant_transaction_session
from app.integrations.channex.client import ChannexApiError, ChannexClient
from app.integrations.channex.rate_value import channex_rate_string
from app.models.core.property import Property
from app.models.integrations.channex_ari_push_log import ChannexAriPushLog
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.rates.availability_ledger import AvailabilityLedger
from app.models.rates.rate import Rate
from app.services.channex_service import _client_for_link, _get_link
from app.worker import celery_app

log = structlog.get_logger()

_BATCH = 200
_SYNC_DAYS = 365
_BATCH_DELAY_SEC = 0.5


async def _sleep_between_batches() -> None:
    await asyncio.sleep(_BATCH_DELAY_SEC)


def _restriction_row(
    prop_cx: str,
    cx_rp: str,
    d: date,
    rrow: Rate,
    currency_code: str,
) -> dict[str, object] | None:
    dec = rrow.price
    if dec <= 0:
        return None
    out: dict[str, object] = {
        "property_id": prop_cx,
        "rate_plan_id": cx_rp,
        "date": d.isoformat(),
        "rate": channex_rate_string(dec, currency_code),
        "stop_sell": rrow.stop_sell,
    }
    if rrow.min_stay_arrival is not None:
        out["min_stay_arrival"] = rrow.min_stay_arrival
    if rrow.max_stay is not None:
        out["max_stay"] = rrow.max_stay
    return out


async def _run_channex_full_ari_sync(tenant_id: UUID, property_id: UUID) -> None:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            link_row = await _get_link(session, tenant_id, property_id)
            if link_row is None:
                log.warning(
                    "channex_ari_sync_no_link",
                    tenant_id=str(tenant_id),
                    property_id=str(property_id),
                )
                return
            if link_row.status != "active":
                log.warning(
                    "channex_ari_sync_skip_not_active",
                    status=link_row.status,
                )
                return

            currency_raw = await session.scalar(
                select(Property.currency).where(
                    Property.tenant_id == tenant_id,
                    Property.id == property_id,
                ),
            )
            currency_code = (currency_raw or "USD").strip().upper()[:3]
            if len(currency_code) != 3:
                currency_code = "USD"

            stmt_maps = select(ChannexRoomTypeMap).where(
                ChannexRoomTypeMap.tenant_id == tenant_id,
                ChannexRoomTypeMap.property_link_id == link_row.id,
            )
            room_maps = list((await session.execute(stmt_maps)).scalars().all())
            if not room_maps:
                log.warning("channex_ari_sync_no_room_maps")
                link_row.last_sync_at = datetime.now(timezone.utc)
                link_row.error_message = None
                await session.flush()
                return

            map_ids = [m.id for m in room_maps]
            stmt_rates = select(ChannexRatePlanMap).where(
                ChannexRatePlanMap.tenant_id == tenant_id,
                ChannexRatePlanMap.room_type_map_id.in_(map_ids),
            )
            rate_maps = list((await session.execute(stmt_rates)).scalars().all())

            rt_ids = [m.room_type_id for m in room_maps]
            start = datetime.now(timezone.utc).date()
            end = start + timedelta(days=_SYNC_DAYS - 1)

            stmt_al = select(AvailabilityLedger).where(
                AvailabilityLedger.tenant_id == tenant_id,
                AvailabilityLedger.room_type_id.in_(rt_ids),
                AvailabilityLedger.date >= start,
                AvailabilityLedger.date <= end,
            )
            avail_map: dict[tuple[UUID, date], int] = {}
            for row in (await session.execute(stmt_al)).scalars().all():
                avail = row.total_rooms - row.booked_rooms - row.blocked_rooms
                avail_map[(row.room_type_id, row.date)] = max(0, avail)

            rate_rows_by_key: dict[tuple[UUID, UUID, date], Rate] = {}
            if rate_maps:
                rp_ids = {m.rate_plan_id for m in rate_maps}
                stmt_r = select(Rate).where(
                    Rate.tenant_id == tenant_id,
                    Rate.room_type_id.in_(rt_ids),
                    Rate.rate_plan_id.in_(rp_ids),
                    Rate.date >= start,
                    Rate.date <= end,
                )
                for row in (await session.execute(stmt_r)).scalars().all():
                    rate_rows_by_key[(row.room_type_id, row.rate_plan_id, row.date)] = (
                        row
                    )

            prop_cx = link_row.channex_property_id.strip()
            av_values: list[dict[str, object]] = []
            for rm in room_maps:
                cx_rt = rm.channex_room_type_id.strip()
                for day_i in range(_SYNC_DAYS):
                    d = start + timedelta(days=day_i)
                    avail = avail_map.get((rm.room_type_id, d), 0)
                    av_values.append(
                        {
                            "property_id": prop_cx,
                            "room_type_id": cx_rt,
                            "date": d.isoformat(),
                            "availability": avail,
                        }
                    )

            rest_values: list[dict[str, object]] = []
            for rpm in rate_maps:
                rtm = next(
                    (x for x in room_maps if x.id == rpm.room_type_map_id),
                    None,
                )
                if rtm is None:
                    continue
                cx_rp = rpm.channex_rate_plan_id.strip()
                rt_open = rtm.room_type_id
                for day_i in range(_SYNC_DAYS):
                    d = start + timedelta(days=day_i)
                    rrow = rate_rows_by_key.get((rt_open, rpm.rate_plan_id, d))
                    if rrow is None:
                        continue
                    built = _restriction_row(
                        prop_cx,
                        cx_rp,
                        d,
                        rrow,
                        currency_code,
                    )
                    if built is not None:
                        rest_values.append(built)

            client: ChannexClient = _client_for_link(link_row)
            try:
                for i in range(0, len(av_values), _BATCH):
                    await client.push_availability(av_values[i : i + _BATCH])
                    await _sleep_between_batches()
                for i in range(0, len(rest_values), _BATCH):
                    await client.push_restrictions(rest_values[i : i + _BATCH])
                    await _sleep_between_batches()
            except ChannexApiError as exc:
                msg = exc.args[0] if exc.args else "Channex API error"
                link_row.error_message = msg[:2000]
                await session.flush()
                log.exception("channex_ari_sync_failed", error=msg)
                # Do not re-raise: ``session.begin()`` would roll back and hide the error.
                return
            except Exception as exc:
                msg = str(exc)[:2000]
                link_row.error_message = msg
                await session.flush()
                log.exception("channex_ari_sync_unexpected", error=msg)
                return

            link_row.last_sync_at = datetime.now(timezone.utc)
            link_row.error_message = None
            session.add(
                ChannexAriPushLog(
                    tenant_id=tenant_id,
                    property_link_id=link_row.id,
                    request_payload={
                        "availability_objects": len(av_values),
                        "restriction_objects": len(rest_values),
                        "date_from": start.isoformat(),
                        "date_to": end.isoformat(),
                    },
                    response_status=200,
                    response_body="ok",
                ),
            )
            await session.flush()
            log.info(
                "channex_ari_sync_ok",
                availability=len(av_values),
                restrictions=len(rest_values),
            )
    finally:
        await engine.dispose()


@celery_app.task(name="channex_full_ari_sync")
def channex_full_ari_sync(tenant_id: str, property_id: str) -> None:
    asyncio.run(
        _run_channex_full_ari_sync(UUID(tenant_id), UUID(property_id)),
    )


async def _run_channex_full_ari_sync_all_properties() -> int:
    """Enqueue full ARI sync for every active Channex link (RLS bypass via SQL function)."""
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    pairs: list[tuple[UUID, UUID]] = []
    try:
        async with factory() as session:
            async with session.begin():
                res = await session.execute(
                    text(
                        "SELECT tenant_id, property_id "
                        "FROM lookup_active_channex_property_links_for_worker()",
                    ),
                )
                for row in res.all():
                    pairs.append((row[0], row[1]))
    finally:
        await engine.dispose()

    enqueued = 0
    for tid, pid in pairs:
        channex_full_ari_sync.delay(str(tid), str(pid))
        enqueued += 1

    log.info(
        "channex_full_ari_sync_all_enqueued",
        active_links=len(pairs),
        enqueued=enqueued,
    )
    return enqueued


@celery_app.task(name="channex_full_ari_sync_all_properties")
def channex_full_ari_sync_all_properties() -> None:
    """Nightly fanout: scheduled by Celery Beat (see worker beat_schedule)."""
    asyncio.run(_run_channex_full_ari_sync_all_properties())
