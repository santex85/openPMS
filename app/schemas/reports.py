"""Management report request/response models (occupancy, revenue, KPI)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ReportRangeParams(BaseModel):
    """Inclusive date range for property reports (max 366 days)."""

    date_from: date
    date_to: date

    @model_validator(mode="after")
    def date_range_ok(self) -> "ReportRangeParams":
        if self.date_to < self.date_from:
            raise ValueError("date_to must be >= date_from")
        span = (self.date_to - self.date_from).days + 1
        if span > 366:
            raise ValueError("date range must not exceed 366 days")
        return self


class OccupancyRow(BaseModel):
    date: date
    occupied_rooms: int
    available_rooms: int
    occupancy_pct: str = Field(
        description="Occupied / available as percent string with 2 dp (0.00 if none).",
    )


class OccupancyReport(BaseModel):
    property_id: UUID
    date_from: date
    date_to: date
    currency: str
    rows: list[OccupancyRow]


class RevenueRow(BaseModel):
    date: date
    room_revenue: str
    other_charges: dict[str, str] = Field(
        description="Charge category code → amount (excludes room_charge and tax).",
    )
    tax_total: str
    payments_total: str


class RevenueReport(BaseModel):
    property_id: UUID
    date_from: date
    date_to: date
    currency: str
    rows: list[RevenueRow]
    room_revenue_total: str
    other_charges_total: dict[str, str]
    tax_total: str
    payments_total: str


class KpiReport(BaseModel):
    property_id: UUID
    date_from: date
    date_to: date
    currency: str
    sold_nights: int
    available_nights: int
    room_revenue: str
    occupancy_pct: str
    adr: str
    revpar: str
