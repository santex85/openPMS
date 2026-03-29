"""Write audit rows using request-scoped AuditContext when present."""

from __future__ import annotations

from typing import Any
from uuid import UUID

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
