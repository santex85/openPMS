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
from app.services.channex_service import _client_for_link
from app.worker import celery_app

log = structlog.get_logger()


async def _run_channex_process_webhook(webhook_log_id: UUID) -> None:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
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

                if (
                    link_row is not None
                    and revision_id
                    and ev_raw
                    in ("booking", "booking_new", "booking_modification")
                ):
                    client = _client_for_link(link_row)
                    try:
                        await client.get_booking_revision(revision_id)
                        await client.acknowledge_revision(revision_id)
                        log.info("channex_webhook_booking_ack", revision_id=revision_id)
                    except ChannexApiError as exc:
                        log.warning(
                            "channex_webhook_booking_channex_error",
                            error=str(exc),
                            revision_id=revision_id,
                        )
                elif ev_raw == "ari":
                    log.info("channex_webhook_ari_event")
                elif not cx_prop:
                    log.warning("channex_webhook_no_property_in_payload")

                wl.processed = True
    finally:
        await engine.dispose()


@celery_app.task(name="channex_process_webhook")
def channex_process_webhook(webhook_log_id: str) -> None:
    asyncio.run(_run_channex_process_webhook(UUID(webhook_log_id)))
