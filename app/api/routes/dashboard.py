"""Operational dashboard (property KPIs)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import BOOKINGS_READ
from app.schemas.dashboard import DashboardSummaryRead
from app.services.dashboard_service import DashboardServiceError, get_dashboard_summary

router = APIRouter()

DashboardReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "housekeeper", "receptionist")),
    Depends(require_scopes(BOOKINGS_READ)),
]


@router.get(
    "/summary",
    response_model=DashboardSummaryRead,
    summary="Property dashboard KPIs",
)
async def get_dashboard_summary_endpoint(
    _: DashboardReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property to summarize"),
) -> DashboardSummaryRead:
    try:
        return await get_dashboard_summary(session, tenant_id, property_id)
    except DashboardServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
