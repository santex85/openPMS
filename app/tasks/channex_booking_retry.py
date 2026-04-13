"""Celery task: retry ingest for a Channex booking revision stored in error (saved payload)."""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import create_async_engine_and_sessionmaker
from app.integrations.channex.client import ChannexApiError
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.services.channex_booking_service import (
    ChannexIngestResult,
    ingest_channex_booking,
)
from app.services.channex_service import _client_for_link
from app.worker import celery_app

log = structlog.get_logger()


async def _run_channex_retry_booking_revision(
    openpms_revision_id: UUID,
    tenant_id: UUID,
) -> None:
    settings = get_settings()
    engine, factory = create_async_engine_and_sessionmaker(settings)
    ingest_out: ChannexIngestResult | None = None
    ack_revision_id: str | None = None
    ack_link_id: UUID | None = None
    ack_tenant_id: UUID | None = None

    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                rev = await session.get(ChannexBookingRevision, openpms_revision_id)
                if rev is None or rev.tenant_id != tenant_id:
                    log.warning(
                        "channex_retry_revision_missing_or_tenant_mismatch",
                        revision_id=str(openpms_revision_id),
                    )
                    return
                if rev.processing_status != "error":
                    log.info(
                        "channex_retry_revision_not_error",
                        revision_id=str(openpms_revision_id),
                        status=rev.processing_status,
                    )
                    return
                link_row = await session.get(ChannexPropertyLink, rev.property_link_id)
                if link_row is None:
                    log.warning(
                        "channex_retry_link_missing",
                        revision_id=str(openpms_revision_id),
                        link_id=str(rev.property_link_id),
                    )
                    return
                ack_revision_id = rev.channex_revision_id
                ack_link_id = link_row.id
                ack_tenant_id = tenant_id
                payload = rev.payload if isinstance(rev.payload, dict) else {}
                ingest_out = await ingest_channex_booking(
                    session,
                    tenant_id,
                    link_row,
                    payload,
                )
                if ingest_out is not None and not ingest_out.success:
                    log.warning(
                        "channex_retry_ingest_still_error",
                        revision_id=str(openpms_revision_id),
                        channex_revision_id=ack_revision_id,
                    )
    finally:
        await engine.dispose()

    if ack_revision_id and ack_link_id is not None and ack_tenant_id is not None:
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
                    log.info("channex_retry_booking_ack", revision_id=ack_revision_id)
            except ChannexApiError as exc:
                log.warning(
                    "channex_retry_booking_ack_failed",
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


@celery_app.task(name="channex_retry_booking_revision")
def channex_retry_booking_revision(openpms_revision_id: str, tenant_id: str) -> None:
    """Replay ``ingest_channex_booking`` from the stored revision ``payload``.

    ``tenant_id`` is required so the worker session can set RLS (``app.tenant_id``)
    before loading ``channex_booking_revisions`` rows.
    """
    asyncio.run(
        _run_channex_retry_booking_revision(
            UUID(openpms_revision_id),
            UUID(tenant_id),
        ),
    )
