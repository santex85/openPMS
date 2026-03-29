"""Nightly rates (prices) API."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, TenantIdDep, require_roles
from app.schemas.nightly_rates import BulkRatesPutRequest, BulkRatesPutResponse, RateRead
from app.services.rates_admin_service import RatesServiceError, bulk_upsert_rates, list_rates_for_period

router = APIRouter(prefix="/rates", tags=["rates"])

RatesReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
]
RatesWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager")),
]



@router.get("", response_model=list[RateRead])
async def get_rates(
    _: RatesReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    room_type_id: UUID = Query(...),
    rate_plan_id: UUID = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
) -> list[RateRead]:
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
async def put_rates_bulk(
    _: RatesWriteRolesDep,
    body: BulkRatesPutRequest,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> BulkRatesPutResponse:
    try:
        n = await bulk_upsert_rates(session, tenant_id, body)
    except RatesServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return BulkRatesPutResponse(rows_upserted=n)
