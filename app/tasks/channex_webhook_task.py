"""Celery task: process logged inbound Channex webhook (booking / ari)."""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import create_async_engine_and_sessionmaker
from app.integrations.channex.client import ChannexApiError
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_webhook_log import ChannexWebhookLog
from app.services.channex_booking_service import (
    ChannexIngestResult,
    ingest_channex_booking,
)
from app.services.email_service import dispatch_channex_booking_emails
from app.services.channex_service import _client_for_link
from app.worker import celery_app

log = structlog.get_logger()

_BOOKING_WEBHOOK_EVENTS = frozenset(
    {
        "booking",
        "booking_new",
        "booking_modification",
        "booking_mod",
        "booking_cancel",
        "booking_cancelled",
        "booking_cancellation",
    },
)


async def _run_channex_process_webhook(webhook_log_id: UUID) -> None:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    ingest_out: ChannexIngestResult | None = None
    ack_should_run = False
    ack_revision_id: str | None = None
    ack_link_id: UUID | None = None
    ack_tenant_id: UUID | None = None

    try:
        async with factory() as session:
            async with session.begin():
                wl = await session.get(ChannexWebhookLog, webhook_log_id)
                if wl is None:
                    return
                payload = wl.payload or {}
                ev_raw = str(payload.get("event") or "").lower()
                inner = payload.get("payload")
                if not isinstance(inner, dict):
                    inner = {}

                revision_id = inner.get("id")
                if isinstance(revision_id, str):
                    revision_id = revision_id.strip() or None
                elif revision_id is not None:
                    revision_id = str(revision_id).strip() or None

                cx_prop = str(payload.get("property_id") or "").strip()
                link_row: ChannexPropertyLink | None = None
                tenant_for_link: UUID | None = None
                if cx_prop:
                    res = await session.execute(
                        text(
                            "SELECT tenant_id, link_id FROM "
                            "lookup_channex_link_for_webhook(:p)",
                        ),
                        {"p": cx_prop},
                    )
                    row = res.first()
                    if row is not None:
                        tid, lid = row[0], row[1]
                        if wl.tenant_id is None:
                            wl.tenant_id = tid
                        await session.execute(
                            text(
                                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                            ),
                            {"tid": str(tid)},
                        )
                        link_row = await session.get(ChannexPropertyLink, lid)
                        tenant_for_link = tid

                if (
                    link_row is not None
                    and tenant_for_link is not None
                    and revision_id
                    and ev_raw in _BOOKING_WEBHOOK_EVENTS
                ):
                    client = _client_for_link(link_row)
                    try:
                        rev_flat = await client.get_booking_revision_raw(revision_id)
                    except ChannexApiError as exc:
                        log.warning(
                            "channex_webhook_booking_fetch_failed",
                            error=str(exc),
                            revision_id=revision_id,
                        )
                    else:
                        ack_should_run = True
                        ack_revision_id = revision_id
                        ack_link_id = link_row.id
                        ack_tenant_id = tenant_for_link
                        ingest_out = await ingest_channex_booking(
                            session,
                            tenant_for_link,
                            link_row,
                            rev_flat,
                        )
                        if ingest_out.skip_idempotent:
                            log.info(
                                "channex_booking_ingest_skip",
                                revision_id=revision_id,
                            )
                        else:
                            log.info(
                                "channex_booking_ingest",
                                revision_id=revision_id,
                                schedule_push=ingest_out.schedule_availability_push,
                            )
                elif ev_raw == "ari":
                    log.info("channex_webhook_ari_event")
                elif not cx_prop:
                    log.warning("channex_webhook_no_property_in_payload")

                wl.processed = True
        if ingest_out is not None and ingest_out.success:
            await dispatch_channex_booking_emails(factory, ingest_out)
    finally:
        await engine.dispose()

    if (
        ack_should_run
        and ack_revision_id
        and ack_link_id is not None
        and ack_tenant_id is not None
    ):
        ack_engine, ack_factory = create_async_engine_and_sessionmaker(settings)
        client_ack = None
        try:
            async with ack_factory() as ack_session:
                async with ack_session.begin():
                    await ack_session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                        ),
                        {"tid": str(ack_tenant_id)},
                    )
                    lr_ack = await ack_session.get(ChannexPropertyLink, ack_link_id)
                    if lr_ack is None:
                        log.warning(
                            "channex_ack_link_missing",
                            link_id=str(ack_link_id),
                        )
                    else:
                        client_ack = _client_for_link(lr_ack)
        finally:
            await ack_engine.dispose()
        if client_ack is not None:
            try:
                if ingest_out is not None and not ingest_out.success:
                    log.warning(
                        "channex_ack_skipped_due_to_error",
                        revision_id=ack_revision_id,
                    )
                else:
                    await client_ack.acknowledge_revision(ack_revision_id)
                    log.info("channex_webhook_booking_ack", revision_id=ack_revision_id)
            except ChannexApiError as exc:
                log.warning(
                    "channex_webhook_booking_ack_failed",
                    error=str(exc),
                    revision_id=ack_revision_id,
                )

    if (
        ingest_out is not None
        and ingest_out.schedule_availability_push
        and ingest_out.room_type_id is not None
        and ingest_out.date_strs
    ):
        from app.tasks.channex_incremental_ari import push_channex_availability

        push_channex_availability.delay(
            str(ingest_out.tenant_id),
            str(ingest_out.property_id),
            str(ingest_out.room_type_id),
            list(ingest_out.date_strs),
        )


@celery_app.task(name="channex_process_webhook")
def channex_process_webhook(webhook_log_id: str) -> None:
    asyncio.run(_run_channex_process_webhook(UUID(webhook_log_id)))
