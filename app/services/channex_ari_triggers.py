"""Schedule Channex incremental ARI pushes from FastAPI BackgroundTasks."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from fastapi import BackgroundTasks


def schedule_push_channex_availability(
    background_tasks: BackgroundTasks,
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    dates: Sequence[date],
) -> None:
    """Enqueue Celery task after HTTP response; no-op if dates empty."""
    uniq = sorted({d.isoformat() for d in dates})
    if not uniq:
        return
    background_tasks.add_task(
        _run_push_channex_availability,
        str(tenant_id),
        str(property_id),
        str(room_type_id),
        uniq,
    )


def schedule_push_channex_rates(
    background_tasks: BackgroundTasks,
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    rate_plan_id: UUID,
    dates: Sequence[date],
) -> None:
    uniq = sorted({d.isoformat() for d in dates})
    if not uniq:
        return
    background_tasks.add_task(
        _run_push_channex_rates,
        str(tenant_id),
        str(property_id),
        str(room_type_id),
        str(rate_plan_id),
        uniq,
    )


def schedule_push_channex_stop_sell(
    background_tasks: BackgroundTasks,
    tenant_id: UUID,
    property_id: UUID,
    room_type_id: UUID,
    dates: Sequence[date],
) -> None:
    uniq = sorted({d.isoformat() for d in dates})
    if not uniq:
        return
    background_tasks.add_task(
        _run_push_channex_stop_sell,
        str(tenant_id),
        str(property_id),
        str(room_type_id),
        uniq,
    )


def _run_push_channex_availability(
    tenant_id_s: str,
    property_id_s: str,
    room_type_id_s: str,
    date_strs: list[str],
) -> None:
    from app.tasks.channex_incremental_ari import push_channex_availability

    push_channex_availability.delay(
        tenant_id_s,
        property_id_s,
        room_type_id_s,
        date_strs,
    )


def _run_push_channex_rates(
    tenant_id_s: str,
    property_id_s: str,
    room_type_id_s: str,
    rate_plan_id_s: str,
    date_strs: list[str],
) -> None:
    from app.tasks.channex_incremental_ari import push_channex_rates

    push_channex_rates.delay(
        tenant_id_s,
        property_id_s,
        room_type_id_s,
        rate_plan_id_s,
        date_strs,
    )


def _run_push_channex_stop_sell(
    tenant_id_s: str,
    property_id_s: str,
    room_type_id_s: str,
    date_strs: list[str],
) -> None:
    from app.tasks.channex_incremental_ari import push_channex_stop_sell

    push_channex_stop_sell.delay(
        tenant_id_s,
        property_id_s,
        room_type_id_s,
        date_strs,
    )
