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
