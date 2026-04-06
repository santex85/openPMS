"""Nightly rates (prices) API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import RATES_READ, RATES_WRITE
from app.core.rate_limit import limiter
from app.schemas.nightly_rates import (
    BulkRatesPutRequest,
    BulkRatesPutResponse,
    RateRead,
)
from app.services.audit_service import record_audit
from app.services.rates_admin_service import (
    RatesServiceError,
    bulk_upsert_rates,
    list_rates_for_period,
)
from app.services.webhook_runner import run_rate_updated_webhooks

router = APIRouter(prefix="/rates", tags=["rates"])

RatesReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(RATES_READ)),
]
RatesWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
    Depends(require_scopes(RATES_WRITE)),
]


@router.get("", response_model=list[RateRead])
@limiter.limit("60/minute")
async def get_rates(
    request: Request,
    _: RatesReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    room_type_id: UUID = Query(...),
    rate_plan_id: UUID = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
) -> list[RateRead]:
    _ = request
    try:
        rows = await list_rates_for_period(
            session,
            tenant_id,
            room_type_id=room_type_id,
            rate_plan_id=rate_plan_id,
            start_date=start_date,
            end_date=end_date,
        )
    except RatesServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return [RateRead.model_validate(r) for r in rows]


@router.put("/bulk", response_model=BulkRatesPutResponse)
@limiter.limit("120/minute")
async def put_rates_bulk(
    request: Request,
    background_tasks: BackgroundTasks,
    _: RatesWriteRolesDep,
    body: BulkRatesPutRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> BulkRatesPutResponse:
    try:
        n, updates = await bulk_upsert_rates(session, tenant_id, body)
    except RatesServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="rates.bulk_upsert",
        entity_type="rate",
        new_values={"rows_upserted": n},
    )
    if updates:
        factory = request.app.state.async_session_factory
        background_tasks.add_task(
            run_rate_updated_webhooks,
            factory,
            tenant_id,
            updates,
        )
    return BulkRatesPutResponse(rows_upserted=n)
