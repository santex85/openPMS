"""Write audit rows using request-scoped AuditContext when present."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import get_audit_context
from app.models.audit.audit_log import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
) -> None:
    ctx = get_audit_context()
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=ctx.user_id if ctx else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ctx.ip_address if ctx else None,
        ),
    )


async def list_audit_logs(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    action: str | None = None,
    entity_type: str | None = None,
) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if action is not None and action.strip():
        stmt = stmt.where(AuditLog.action == action.strip())
    if entity_type is not None and entity_type.strip():
        stmt = stmt.where(AuditLog.entity_type == entity_type.strip())
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
