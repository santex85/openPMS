"""Read access to email_logs for bookings (TZ-16)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications.email_log import EmailLog


async def list_email_logs_for_booking(
    session: AsyncSession,
    tenant_id: UUID,
    booking_id: UUID,
) -> list[EmailLog]:
    stmt = (
        select(EmailLog)
        .where(
            EmailLog.tenant_id == tenant_id,
            EmailLog.booking_id == booking_id,
        )
        .order_by(EmailLog.sent_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
