"""Top-level unpaid folio summary (avoids /bookings/{{booking_id}} swallowing nested paths)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import BOOKINGS_READ
from app.core.rate_limit import limiter
from app.schemas.bookings import BookingUnpaidFolioSummaryRead
from app.services.folio_service import list_unpaid_folio_summary_for_property
from app.services.room_list_service import property_belongs_to_tenant

router = APIRouter(tags=["bookings"])

UnpaidFolioReadRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles(
                "owner",
                "manager",
                "viewer",
                "housekeeper",
                "receptionist",
            ),
            require_scopes(BOOKINGS_READ),
        ),
    ),
]


@router.get(
    "/unpaid-folio-summary",
    response_model=list[BookingUnpaidFolioSummaryRead],
)
@limiter.limit("60/minute")
async def get_unpaid_folio_summary_at_root(
    request: Request,
    _: UnpaidFolioReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(..., description="Property scope"),
) -> list[BookingUnpaidFolioSummaryRead]:
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    if not await property_belongs_to_tenant(session, tenant_id, property_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="property not found",
        )
    raw = await list_unpaid_folio_summary_for_property(session, tenant_id, property_id)
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
