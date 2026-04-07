"""Country pack extensions: registry, property activation, check-in validation."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.integrations.country_pack_extension import CountryPackExtension
from app.models.integrations.property_extension import PropertyExtension
from app.schemas.country_pack import ExtensionCreate, ExtensionRead, PropertyExtensionRead


class ExtensionServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def register_extension(
    session: AsyncSession,
    tenant_id: UUID,
    data: ExtensionCreate,
) -> ExtensionRead:
    code = data.code.strip()
    existing = await session.scalar(
        select(CountryPackExtension).where(
            CountryPackExtension.tenant_id == tenant_id,
            CountryPackExtension.code == code,
        ),
    )
    if existing is not None:
        raise ExtensionServiceError("extension code already registered", status_code=409)

    row = CountryPackExtension(
        tenant_id=tenant_id,
        code=code,
        name=data.name.strip(),
        country_code=data.country_code,
        webhook_url=data.webhook_url.strip(),
        required_fields=list(data.required_fields),
        ui_config_schema=data.ui_config_schema,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return ExtensionRead.model_validate(row)


async def list_extensions(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    country_code: str | None = None,
) -> list[ExtensionRead]:
    stmt = select(CountryPackExtension).where(
        CountryPackExtension.tenant_id == tenant_id,
    )
    if country_code is not None:
        cc = country_code.strip().upper()
        stmt = stmt.where(
            (CountryPackExtension.country_code == cc)
            | (CountryPackExtension.country_code.is_(None)),
        )
    stmt = stmt.order_by(CountryPackExtension.code)
    result = await session.execute(stmt)
    return [ExtensionRead.model_validate(r) for r in result.scalars().all()]


async def upsert_property_extension(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    extension_id: UUID,
    *,
    config: dict[str, Any] | None,
    is_active: bool,
) -> PropertyExtensionRead:
    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise ExtensionServiceError("property not found", status_code=404)

    ext = await session.scalar(
        select(CountryPackExtension).where(
            CountryPackExtension.tenant_id == tenant_id,
            CountryPackExtension.id == extension_id,
        ),
    )
    if ext is None:
        raise ExtensionServiceError("extension not found", status_code=404)

    row = await session.scalar(
        select(PropertyExtension).where(
            PropertyExtension.tenant_id == tenant_id,
            PropertyExtension.property_id == property_id,
            PropertyExtension.extension_id == extension_id,
        ),
    )
    if row is None:
        row = PropertyExtension(
            tenant_id=tenant_id,
            property_id=property_id,
            extension_id=extension_id,
            config=config,
            is_active=is_active,
        )
        session.add(row)
    else:
        row.config = config
        row.is_active = is_active
    await session.flush()
    return PropertyExtensionRead.model_validate(row)


async def list_property_extensions(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> list[PropertyExtensionRead]:
    stmt = (
        select(PropertyExtension)
        .where(
            PropertyExtension.tenant_id == tenant_id,
            PropertyExtension.property_id == property_id,
        )
        .order_by(PropertyExtension.created_at.asc())
    )
    result = await session.execute(stmt)
    return [PropertyExtensionRead.model_validate(r) for r in result.scalars().all()]


def _guest_field_present(guest: Guest, field: str) -> bool:
    raw = getattr(guest, field, None)
    if raw is None:
        return False
    if isinstance(raw, str) and not raw.strip():
        return False
    return True


async def validate_extension_required_fields_for_checkin(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    guest: Guest,
) -> list[str]:
    """
    Return human-readable error parts if any active property extension requires guest data.
    Empty list means OK.
    """
    pe_result = await session.execute(
        select(PropertyExtension).where(
            PropertyExtension.tenant_id == tenant_id,
            PropertyExtension.property_id == property_id,
            PropertyExtension.is_active.is_(True),
        ),
    )
    links = list(pe_result.scalars().all())
    if not links:
        return []

    ext_ids = [ln.extension_id for ln in links]
    ext_rows = (
        await session.execute(
            select(CountryPackExtension).where(
                CountryPackExtension.tenant_id == tenant_id,
                CountryPackExtension.id.in_(ext_ids),
                CountryPackExtension.is_active.is_(True),
            ),
        )
    ).scalars().all()
    ext_by_id = {e.id: e for e in ext_rows}

    missing_messages: list[str] = []
    for ln in links:
        ext = ext_by_id.get(ln.extension_id)
        if ext is None or not ext.is_active:
            continue
        rf = ext.required_fields
        if not isinstance(rf, list):
            continue
        missing: list[str] = []
        for fname in rf:
            key = str(fname).strip()
            if not key:
                continue
            if not _guest_field_present(guest, key):
                missing.append(key)
        if missing:
            missing_messages.append(
                f"{ext.code} requires: {', '.join(missing)}",
            )
    return missing_messages
