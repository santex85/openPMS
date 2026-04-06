"""Read-only audit trail (owner / manager, JWT only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import SessionDep, TenantIdDep, require_jwt_user, require_roles
from app.core.rate_limit import limiter
from app.schemas.audit import AuditLogItemRead
from app.services.audit_service import list_audit_logs

router = APIRouter()

AuditReadDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
]


@router.get(
    "",
    response_model=list[AuditLogItemRead],
    summary="List recent audit events for the tenant",
)
@limiter.limit("60/minute")
async def get_audit_log(
    request: Request,
    _: AuditReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, description="Exact match on action"),
    entity_type: str | None = Query(None, description="Exact match on entity_type"),
) -> list[AuditLogItemRead]:
    _ = request
    rows = await list_audit_logs(
        session,
        tenant_id,
        limit=limit,
        offset=offset,
        action=action,
        entity_type=entity_type,
    )
    return [AuditLogItemRead.model_validate(r) for r in rows]
