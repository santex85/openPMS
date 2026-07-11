"""Property management reports (occupancy, revenue, KPI) with optional CSV export."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import BOOKINGS_READ
from app.core.rate_limit import limiter
from app.schemas.reports import (
    KpiReport,
    OccupancyReport,
    ReportRangeParams,
    RevenueReport,
)
from app.services.reports_service import (
    ReportsServiceError,
    get_kpi_report,
    get_occupancy_report,
    get_revenue_report,
    kpi_report_csv_rows,
    occupancy_report_csv_rows,
    revenue_report_csv_rows,
)

router = APIRouter(prefix="/properties/{property_id}/reports", tags=["reports"])

ReportsReadRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(BOOKINGS_READ),
        ),
    ),
]


def _range_params(
    date_from: date = Query(..., description="Inclusive range start"),
    date_to: date = Query(..., description="Inclusive range end"),
) -> ReportRangeParams:
    try:
        return ReportRangeParams(date_from=date_from, date_to=date_to)
    except ValidationError as exc:
        messages = [str(err.get("msg", "validation error")) for err in exc.errors()]
        raise HTTPException(
            status_code=422,
            detail="; ".join(messages) if messages else "invalid date range",
        ) from exc


def _csv_response(
    filename: str,
    body: object,
) -> StreamingResponse:
    return StreamingResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/occupancy",
    response_model=OccupancyReport,
    summary="Occupancy report by day",
    responses={200: {"content": {"text/csv": {}}}},
)
@limiter.limit("60/minute")
async def get_occupancy_report_endpoint(
    request: Request,
    _: ReportsReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID,
    params: Annotated[ReportRangeParams, Depends(_range_params)],
    format: Literal["json", "csv"] = Query("json"),
) -> OccupancyReport | StreamingResponse:
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    try:
        report = await get_occupancy_report(
            session,
            tenant_id,
            property_id,
            params.date_from,
            params.date_to,
        )
    except ReportsServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if format == "csv":
        return _csv_response(
            f"occupancy_{params.date_from.isoformat()}_{params.date_to.isoformat()}.csv",
            occupancy_report_csv_rows(report),
        )
    return report


@router.get(
    "/revenue",
    response_model=RevenueReport,
    summary="Revenue report by day",
    responses={200: {"content": {"text/csv": {}}}},
)
@limiter.limit("60/minute")
async def get_revenue_report_endpoint(
    request: Request,
    _: ReportsReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID,
    params: Annotated[ReportRangeParams, Depends(_range_params)],
    format: Literal["json", "csv"] = Query("json"),
) -> RevenueReport | StreamingResponse:
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    try:
        report = await get_revenue_report(
            session,
            tenant_id,
            property_id,
            params.date_from,
            params.date_to,
        )
    except ReportsServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if format == "csv":
        return _csv_response(
            f"revenue_{params.date_from.isoformat()}_{params.date_to.isoformat()}.csv",
            revenue_report_csv_rows(report),
        )
    return report


@router.get(
    "/kpi",
    response_model=KpiReport,
    summary="ADR / RevPAR KPI report",
    responses={200: {"content": {"text/csv": {}}}},
)
@limiter.limit("60/minute")
async def get_kpi_report_endpoint(
    request: Request,
    _: ReportsReadRolesDep,
    response: Response,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID,
    params: Annotated[ReportRangeParams, Depends(_range_params)],
    format: Literal["json", "csv"] = Query("json"),
) -> KpiReport | StreamingResponse:
    _ = request
    response.headers["Cache-Control"] = "private, no-store"
    try:
        report = await get_kpi_report(
            session,
            tenant_id,
            property_id,
            params.date_from,
            params.date_to,
        )
    except ReportsServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if format == "csv":
        return _csv_response(
            f"kpi_{params.date_from.isoformat()}_{params.date_to.isoformat()}.csv",
            kpi_report_csv_rows(report),
        )
    return report
