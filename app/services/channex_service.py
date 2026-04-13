"""Channex onboarding: connect, map rooms/rates, status."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.channex.client import ChannexApiError, ChannexClient
from app.integrations.channex.crypto import (
    decrypt_channex_api_key,
    encrypt_channex_api_key,
)
from app.integrations.channex.schemas import ChannexProperty
from app.models.integrations.channex_booking_revision import ChannexBookingRevision
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.models.integrations.channex_rate_plan_map import ChannexRatePlanMap
from app.models.integrations.channex_room_type_map import ChannexRoomTypeMap
from app.models.core.room_type import RoomType
from app.models.rates.rate_plan import RatePlan
from app.schemas.channex import (
    ChannexPropertyLinkRead,
    ChannexProvisionRead,
    ChannexRatePlanRead,
    ChannexRoomTypeMapRead,
    ChannexRoomTypeRead,
    ChannexStatusRead,
    RateMappingItem,
    RoomMappingItem,
)
from app.services import property_service
from app.services import rate_plan_service, room_type_service


class ChannexServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _normalize_env(env: str) -> str:
    e = (env or "production").strip().lower()
    if e not in ("production", "sandbox"):
        raise ChannexServiceError(
            "env must be 'production' or 'sandbox'",
            status_code=422,
        )
    return e


def _channex_http_to_service(exc: ChannexApiError) -> ChannexServiceError:
    code = exc.status_code or 502
    if code in (401, 403):
        return ChannexServiceError(
            "Invalid or unauthorized Channex API key",
            status_code=401,
        )
    if code == 404:
        return ChannexServiceError("Channex resource not found", status_code=404)
    if code == 422:
        detail = "Channex rejected the request (validation error)"
        if exc.body and len(exc.body) < 800:
            detail = exc.body
        return ChannexServiceError(detail, status_code=422)
    return ChannexServiceError(
        exc.args[0] if exc.args else "Channex API error",
        status_code=502,
    )


async def validate_key(api_key: str, env: str) -> list[ChannexProperty]:
    env_n = _normalize_env(env)
    client = ChannexClient(api_key.strip(), env=env_n)
    try:
        return await client.get_properties()
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc


async def create_channex_property_from_openpms(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    api_key: str,
    env: str,
) -> ChannexProperty:
    """Create a property in Channex using the OpenPMS hotel name, currency, timezone."""
    env_n = _normalize_env(env)
    existing = await _get_link(session, tenant_id, property_id)
    if existing is not None:
        raise ChannexServiceError(
            "Channex is already connected for this property",
            status_code=409,
        )
    prop = await property_service.get_property(session, tenant_id, property_id)
    if prop is None:
        raise ChannexServiceError("Property not found", status_code=404)

    client = ChannexClient(api_key.strip(), env=env_n)
    try:
        return await client.create_property(
            prop.name,
            prop.currency,
            prop.timezone,
        )
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc


async def _get_link(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> ChannexPropertyLink | None:
    stmt = select(ChannexPropertyLink).where(
        ChannexPropertyLink.tenant_id == tenant_id,
        ChannexPropertyLink.property_id == property_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def connect(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    api_key: str,
    env: str,
    channex_property_id: str,
) -> ChannexPropertyLink:
    env_n = _normalize_env(env)
    key_plain = api_key.strip()
    cx_prop_id = channex_property_id.strip()

    existing = await _get_link(session, tenant_id, property_id)
    if existing is not None:
        raise ChannexServiceError(
            "Channex is already connected for this property",
            status_code=409,
        )

    props = await validate_key(key_plain, env_n)
    if not any(p.id == cx_prop_id for p in props):
        raise ChannexServiceError(
            "Selected Channex property is not available for this API key",
            status_code=422,
        )

    settings = get_settings()
    encrypted = encrypt_channex_api_key(settings, key_plain)
    row = ChannexPropertyLink(
        tenant_id=tenant_id,
        property_id=property_id,
        channex_property_id=cx_prop_id,
        channex_api_key=encrypted,
        channex_env=env_n,
        status="pending",
    )
    session.add(row)
    await session.flush()
    return row


async def get_status(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> ChannexStatusRead:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        return ChannexStatusRead(
            connected=False,
            link=None,
            room_maps_count=0,
            rate_maps_count=0,
            room_type_maps=[],
        )

    rt_stmt = (
        select(ChannexRoomTypeMap)
        .where(
            ChannexRoomTypeMap.property_link_id == link.id,
            ChannexRoomTypeMap.tenant_id == tenant_id,
        )
        .order_by(ChannexRoomTypeMap.created_at)
    )
    rt_result = await session.execute(rt_stmt)
    rt_rows = list(rt_result.scalars().all())
    room_n = len(rt_rows)

    room_type_maps = [ChannexRoomTypeMapRead.model_validate(r) for r in rt_rows]
    rate_n = await session.scalar(
        select(func.count())
        .select_from(ChannexRatePlanMap)
        .where(
            ChannexRatePlanMap.tenant_id == tenant_id,
            ChannexRatePlanMap.room_type_map_id.in_(
                select(ChannexRoomTypeMap.id).where(
                    ChannexRoomTypeMap.property_link_id == link.id,
                    ChannexRoomTypeMap.tenant_id == tenant_id,
                ),
            ),
        ),
    )
    return ChannexStatusRead(
        connected=True,
        link=ChannexPropertyLinkRead.model_validate(link),
        room_maps_count=int(room_n or 0),
        rate_maps_count=int(rate_n or 0),
        room_type_maps=room_type_maps,
    )


def _client_for_link(link: ChannexPropertyLink) -> ChannexClient:
    settings = get_settings()
    plain = decrypt_channex_api_key(settings, link.channex_api_key)
    return ChannexClient(plain, env=link.channex_env)


async def get_channex_rooms(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> list[ChannexRoomTypeRead]:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property", status_code=404
        )
    client = _client_for_link(link)
    try:
        items = await client.get_room_types(link.channex_property_id)
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc
    return [ChannexRoomTypeRead(id=r.id, title=r.title) for r in items]


async def get_channex_rates(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> list[ChannexRatePlanRead]:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property", status_code=404
        )
    client = _client_for_link(link)
    try:
        items = await client.get_rate_plans(link.channex_property_id)
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc
    return [ChannexRatePlanRead(id=r.id, title=r.title) for r in items]


async def provision_channex_from_openpms(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> ChannexProvisionRead:
    """Create missing Channex room types and rate plans from OpenPMS (idempotent by title)."""
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property",
            status_code=404,
        )

    prop = await property_service.get_property(session, tenant_id, property_id)
    if prop is None:
        raise ChannexServiceError("Property not found", status_code=404)

    room_types_local = await room_type_service.list_room_types(
        session,
        tenant_id,
        property_id=property_id,
    )
    if not room_types_local:
        raise ChannexServiceError(
            "No room categories in OpenPMS for this property; add room types first",
            status_code=422,
        )

    client = _client_for_link(link)
    cx_pid = link.channex_property_id

    try:
        existing_cx_rt = await client.get_room_types(cx_pid)
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc

    cx_by_title: dict[str, str] = {}
    for r in existing_cx_rt:
        t = (r.title or "").strip()
        if t and r.id:
            cx_by_title[t] = r.id

    om_rt_to_cx: dict[UUID, str] = {}
    created_rt = 0
    skipped_rt = 0

    for rt in room_types_local:
        key = rt.name.strip()
        if key in cx_by_title:
            om_rt_to_cx[rt.id] = cx_by_title[key]
            skipped_rt += 1
            continue

        count = await room_type_service.count_rooms_for_room_type(
            session,
            tenant_id,
            rt.id,
        )
        count_of_rooms = max(1, count)
        occ_adults = max(1, rt.max_occupancy)
        default_occ = max(1, min(rt.base_occupancy, occ_adults))

        try:
            cx_r = await client.create_room_type(
                property_id=cx_pid,
                title=rt.name,
                count_of_rooms=count_of_rooms,
                occ_adults=occ_adults,
                occ_children=0,
                occ_infants=0,
                default_occupancy=default_occ,
            )
        except ChannexApiError as exc:
            raise _channex_http_to_service(exc) from exc

        om_rt_to_cx[rt.id] = cx_r.id
        cx_by_title[key] = cx_r.id
        created_rt += 1

    rate_plans_local = await rate_plan_service.list_rate_plans(
        session,
        tenant_id,
        property_id=property_id,
    )

    try:
        existing_cx_rp = await client.get_rate_plans(cx_pid)
    except ChannexApiError as exc:
        raise _channex_http_to_service(exc) from exc

    rp_titles_existing = {
        (r.title or "").strip() for r in existing_cx_rp if (r.title or "").strip()
    }

    created_rp = 0
    skipped_rp = 0

    if rate_plans_local:
        currency = (prop.currency or "USD").strip().upper()
        for rp in rate_plans_local:
            for rt in room_types_local:
                cx_rt_id = om_rt_to_cx.get(rt.id)
                if not cx_rt_id:
                    continue
                title = f"{rp.name.strip()} / {rt.name.strip()}"[:255]
                if title in rp_titles_existing:
                    skipped_rp += 1
                    continue
                try:
                    await client.create_rate_plan(
                        property_id=cx_pid,
                        room_type_id=cx_rt_id,
                        title=title,
                        currency=currency,
                        primary_occupancy=max(1, rt.max_occupancy),
                    )
                except ChannexApiError as exc:
                    raise _channex_http_to_service(exc) from exc
                rp_titles_existing.add(title)
                created_rp += 1

    return ChannexProvisionRead(
        room_types_created=created_rt,
        room_types_skipped=skipped_rt,
        rate_plans_created=created_rp,
        rate_plans_skipped=skipped_rp,
    )


async def _assert_room_types_belong_to_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    room_type_ids: list[UUID],
) -> None:
    if not room_type_ids:
        return
    stmt = (
        select(func.count())
        .select_from(RoomType)
        .where(
            RoomType.tenant_id == tenant_id,
            RoomType.property_id == property_id,
            RoomType.id.in_(room_type_ids),
            RoomType.deleted_at.is_(None),
        )
    )
    n = await session.scalar(stmt)
    if int(n or 0) != len(set(room_type_ids)):
        raise ChannexServiceError(
            "One or more room types are invalid for this property",
            status_code=422,
        )


async def save_room_mappings(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    mappings: list[RoomMappingItem],
) -> None:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property", status_code=404
        )

    room_type_ids = [m.room_type_id for m in mappings]
    if len(room_type_ids) != len(set(room_type_ids)):
        raise ChannexServiceError(
            "Duplicate OpenPMS room type in mappings", status_code=422
        )
    channex_ids = [m.channex_room_type_id for m in mappings]
    if len(channex_ids) != len(set(channex_ids)):
        raise ChannexServiceError(
            "Duplicate Channex room type in mappings", status_code=422
        )

    await _assert_room_types_belong_to_property(
        session,
        tenant_id,
        property_id,
        room_type_ids,
    )

    rt_map_ids_subq = select(ChannexRoomTypeMap.id).where(
        ChannexRoomTypeMap.property_link_id == link.id,
        ChannexRoomTypeMap.tenant_id == tenant_id,
    )
    await session.execute(
        delete(ChannexRatePlanMap).where(
            ChannexRatePlanMap.room_type_map_id.in_(rt_map_ids_subq),
            ChannexRatePlanMap.tenant_id == tenant_id,
        ),
    )
    await session.execute(
        delete(ChannexRoomTypeMap).where(
            ChannexRoomTypeMap.property_link_id == link.id,
            ChannexRoomTypeMap.tenant_id == tenant_id,
        ),
    )

    for m in mappings:
        session.add(
            ChannexRoomTypeMap(
                tenant_id=tenant_id,
                property_link_id=link.id,
                room_type_id=m.room_type_id,
                channex_room_type_id=m.channex_room_type_id.strip(),
                channex_room_type_name=m.channex_room_type_name,
            ),
        )
    await session.flush()


async def _assert_rate_plans_belong_to_property(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    rate_plan_ids: list[UUID],
) -> None:
    if not rate_plan_ids:
        return
    stmt = (
        select(func.count())
        .select_from(RatePlan)
        .where(
            RatePlan.tenant_id == tenant_id,
            RatePlan.property_id == property_id,
            RatePlan.id.in_(rate_plan_ids),
        )
    )
    n = await session.scalar(stmt)
    if int(n or 0) != len(set(rate_plan_ids)):
        raise ChannexServiceError(
            "One or more rate plans are invalid for this property",
            status_code=422,
        )


async def save_rate_mappings(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    mappings: list[RateMappingItem],
) -> None:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property", status_code=404
        )

    if not mappings:
        subq = select(ChannexRoomTypeMap.id).where(
            ChannexRoomTypeMap.property_link_id == link.id,
            ChannexRoomTypeMap.tenant_id == tenant_id,
        )
        await session.execute(
            delete(ChannexRatePlanMap).where(
                ChannexRatePlanMap.room_type_map_id.in_(subq),
                ChannexRatePlanMap.tenant_id == tenant_id,
            ),
        )
        await session.flush()
        return

    room_type_map_ids = [m.room_type_map_id for m in mappings]
    rate_plan_ids = [m.rate_plan_id for m in mappings]
    pair_keys = [(m.room_type_map_id, m.rate_plan_id) for m in mappings]
    if len(pair_keys) != len(set(pair_keys)):
        raise ChannexServiceError(
            "Duplicate room category and rate plan combination in mappings",
            status_code=422,
        )

    stmt_maps = select(ChannexRoomTypeMap).where(
        ChannexRoomTypeMap.tenant_id == tenant_id,
        ChannexRoomTypeMap.property_link_id == link.id,
        ChannexRoomTypeMap.id.in_(set(room_type_map_ids)),
    )
    result = await session.execute(stmt_maps)
    found = {row.id for row in result.scalars().all()}
    if found != set(room_type_map_ids):
        raise ChannexServiceError(
            "One or more room type mappings are invalid for this connection",
            status_code=422,
        )

    await _assert_rate_plans_belong_to_property(
        session,
        tenant_id,
        property_id,
        rate_plan_ids,
    )

    subq = select(ChannexRoomTypeMap.id).where(
        ChannexRoomTypeMap.property_link_id == link.id,
        ChannexRoomTypeMap.tenant_id == tenant_id,
    )
    await session.execute(
        delete(ChannexRatePlanMap).where(
            ChannexRatePlanMap.room_type_map_id.in_(subq),
            ChannexRatePlanMap.tenant_id == tenant_id,
        ),
    )

    for m in mappings:
        session.add(
            ChannexRatePlanMap(
                tenant_id=tenant_id,
                room_type_map_id=m.room_type_map_id,
                rate_plan_id=m.rate_plan_id,
                channex_rate_plan_id=m.channex_rate_plan_id.strip(),
                channex_rate_plan_name=m.channex_rate_plan_name,
            ),
        )
    await session.flush()


async def activate(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> ChannexPropertyLink:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property", status_code=404
        )
    now = datetime.now(timezone.utc)
    settings = get_settings()
    webhook_url = (settings.channex_webhook_url or "").strip()
    existing_wid = (link.channex_webhook_id or "").strip()
    if webhook_url and not existing_wid:
        client = _client_for_link(link)
        try:
            resp = await client.create_webhook(webhook_url, ["booking", "ari"])
        except ChannexApiError as exc:
            raise _channex_http_to_service(exc) from exc
        wid = ChannexClient.extract_created_resource_id(resp)
        if not wid:
            raise ChannexServiceError(
                "Channex webhook response missing id",
                status_code=502,
            )
        link.channex_webhook_id = wid
    elif not webhook_url:
        structlog.get_logger().warning(
            "channex_activate_skipping_webhook_no_channex_webhook_url",
        )

    link.status = "active"
    if link.connected_at is None:
        link.connected_at = now
    link.error_message = None
    await session.flush()
    return link


async def require_active_channex_link(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> ChannexPropertyLink:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        raise ChannexServiceError(
            "Channex is not connected for this property",
            status_code=404,
        )
    if link.status != "active":
        raise ChannexServiceError(
            "Channex integration must be active to sync",
            status_code=409,
        )
    return link


async def disconnect(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> None:
    link = await _get_link(session, tenant_id, property_id)
    if link is None:
        return

    wid = (link.channex_webhook_id or "").strip()
    if wid:
        try:
            client = _client_for_link(link)
            await client.delete_webhook(wid)
        except ChannexApiError as exc:
            structlog.get_logger().warning(
                "channex_delete_webhook_failed",
                status=getattr(exc, "status_code", None),
                detail=str(exc)[:500],
            )

    subq = select(ChannexRoomTypeMap.id).where(
        ChannexRoomTypeMap.property_link_id == link.id,
        ChannexRoomTypeMap.tenant_id == tenant_id,
    )
    await session.execute(
        delete(ChannexRatePlanMap).where(
            ChannexRatePlanMap.room_type_map_id.in_(subq),
            ChannexRatePlanMap.tenant_id == tenant_id,
        ),
    )
    await session.execute(
        delete(ChannexRoomTypeMap).where(
            ChannexRoomTypeMap.property_link_id == link.id,
            ChannexRoomTypeMap.tenant_id == tenant_id,
        ),
    )
    await session.execute(
        delete(ChannexPropertyLink).where(
            ChannexPropertyLink.tenant_id == tenant_id,
            ChannexPropertyLink.id == link.id,
        ),
    )
    await session.flush()


async def list_failed_channex_booking_revisions(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    property_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[ChannexBookingRevision, UUID]], int]:
    """List booking revisions in ``error`` for the tenant, newest ``received_at`` first."""
    join_cond = ChannexPropertyLink.id == ChannexBookingRevision.property_link_id
    filters = [
        ChannexBookingRevision.tenant_id == tenant_id,
        ChannexBookingRevision.processing_status == "error",
    ]
    if property_id is not None:
        filters.append(ChannexPropertyLink.property_id == property_id)

    count_stmt = (
        select(func.count())
        .select_from(ChannexBookingRevision)
        .join(ChannexPropertyLink, join_cond)
        .where(*filters)
    )
    total = int((await session.execute(count_stmt)).scalar_one())

    list_stmt = (
        select(ChannexBookingRevision, ChannexPropertyLink.property_id)
        .join(ChannexPropertyLink, join_cond)
        .where(*filters)
        .order_by(ChannexBookingRevision.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(list_stmt)
    rows = [(row[0], row[1]) for row in result.all()]
    return rows, total
