"""Operational dashboard (property KPIs)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import BOOKINGS_READ
from app.core.rate_limit import limiter
from app.schemas.bookings import BookingUnpaidFolioSummaryRead
from app.schemas.dashboard import DashboardSummaryRead
from app.services.dashboard_service import DashboardServiceError, get_dashboard_summary
from app.services.folio_service import list_unpaid_folio_summary_for_property
from app.services.room_list_service import property_belongs_to_tenant

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
@limiter.limit("60/minute")
async def get_dashboard_summary_endpoint(
    request: Request,
    _: DashboardReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property to summarize"),
) -> DashboardSummaryRead:
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await get_dashboard_summary(session, tenant_id, property_id)
    except DashboardServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@router.get(
    "/unpaid-folio-summary",
    response_model=list[BookingUnpaidFolioSummaryRead],
    summary="Bookings with positive folio balance",
)
@limiter.limit("60/minute")
async def get_dashboard_unpaid_folio_summary(
    request: Request,
    _: DashboardReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property scope"),
) -> list[BookingUnpaidFolioSummaryRead]:
    """Alias for GET /bookings/unpaid-folio-summary (avoids /bookings/{{id}} swallow on old builds)."""
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    if not await property_belongs_to_tenant(session, tenant_id, property_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property not found",
        )
    raw = await list_unpaid_folio_summary_for_property(
        session, tenant_id, property_id
    )
    out: list[BookingUnpaidFolioSummaryRead] = []
    for bid, bal, fn, ln in raw:
        name = f"{fn} {ln}".strip()
        out.append(
            BookingUnpaidFolioSummaryRead(
                booking_id=bid,
                balance=format(bal, "f"),
                guest_name=name if name else None,
            ),
        )
    return out
