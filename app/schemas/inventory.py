"""Pydantic models for availability / inventory API."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AvailabilityCell(BaseModel):
    date: date
    room_type_id: UUID
    room_type_name: str
    total_rooms: int
    booked_rooms: int
    blocked_rooms: int
    available_rooms: int = Field(
        ...,
        description="total_rooms - booked_rooms - blocked_rooms",
    )


class AvailabilityGridResponse(BaseModel):
    property_id: UUID
    start_date: date
    end_date: date
    cells: list[AvailabilityCell]


class AvailabilityLedgerSeedSegment(BaseModel):
    room_type_id: UUID
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> "AvailabilityLedgerSeedSegment":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        span = (self.end_date - self.start_date).days + 1
        if span > 366:
            raise ValueError("each segment may cover at most 366 nights")
        return self

    model_config = {"extra": "forbid"}


class BulkAvailabilityLedgerSeedRequest(BaseModel):
    segments: list[AvailabilityLedgerSeedSegment] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_total_span(self) -> "BulkAvailabilityLedgerSeedRequest":
        total = 0
        for seg in self.segments:
            total += (seg.end_date - seg.start_date).days + 1
        if total > 366:
            raise ValueError(
                "total number of ledger rows in one request cannot exceed 366",
            )
        return self

    model_config = {"extra": "forbid"}


class BulkAvailabilityLedgerSeedResponse(BaseModel):
    rows_upserted: int


class AvailabilityQueryParams(BaseModel):
    """Validated query string for GET /inventory/availability."""

    property_id: UUID
    start_date: date
    end_date: date
    room_type_id: UUID | None = None

    @model_validator(mode="after")
    def date_range_ok(self) -> "AvailabilityQueryParams":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        span = (self.end_date - self.start_date).days + 1
        if span > 366:
            raise ValueError("date range must not exceed 366 days")
        return self
