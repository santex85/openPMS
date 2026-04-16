"""CRUD for property-scoped email_settings (TZ-16)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications.email_settings import EmailSettings
from app.schemas.email_settings import EmailSettingsPut


async def get_email_settings(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> EmailSettings | None:
    return await session.scalar(
        select(EmailSettings).where(
            EmailSettings.tenant_id == tenant_id,
            EmailSettings.property_id == property_id,
        ),
    )


async def upsert_email_settings(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    data: EmailSettingsPut,
) -> EmailSettings:
    row = await get_email_settings(session, tenant_id, property_id)
    reply = (data.reply_to or "").strip() or None
    logo = (data.logo_url or "").strip() or None
    if row is None:
        row = EmailSettings(
            tenant_id=tenant_id,
            property_id=property_id,
            sender_name=data.sender_name.strip(),
            reply_to=reply,
            logo_url=logo,
            locale=data.locale.strip().lower(),
        )
        session.add(row)
    else:
        row.sender_name = data.sender_name.strip()
        row.reply_to = reply
        row.logo_url = logo
        row.locale = data.locale.strip().lower()
    await session.flush()
    return row
