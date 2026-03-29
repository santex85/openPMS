"""Operator overrides for availability ledger blocked_rooms."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AvailabilityOverridePutRequest(BaseModel):
    room_type_id: UUID
    start_date: date
    end_date: date
    blocked_rooms: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "AvailabilityOverridePutRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days + 1 > 366:
            raise ValueError("override range cannot exceed 366 nights")
        return self

    model_config = ConfigDict(extra="forbid")


class AvailabilityOverridePutResponse(BaseModel):
    dates_updated: int
    model_config = ConfigDict(extra="forbid")
