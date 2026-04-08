"""Pydantic models for nightly rate (prices) API."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    room_type_id: UUID
    rate_plan_id: UUID
    date: date
    price: Decimal
    stop_sell: bool = False
    min_stay_arrival: int | None = None
    max_stay: int | None = None


class BulkRateSegment(BaseModel):
    room_type_id: UUID
    rate_plan_id: UUID
    start_date: date
    end_date: date
    price: Decimal = Field(..., ge=Decimal("0"))
    stop_sell: bool = False
    min_stay_arrival: int | None = Field(default=None, ge=1)
    max_stay: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "BulkRateSegment":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        span = (self.end_date - self.start_date).days + 1
        if span > 366:
            raise ValueError("each segment may cover at most 366 nights")
        if (
            self.min_stay_arrival is not None
            and self.max_stay is not None
            and self.max_stay < self.min_stay_arrival
        ):
            raise ValueError("max_stay must be >= min_stay_arrival when both are set")
        return self

    model_config = ConfigDict(extra="forbid")


class BulkRatesPutRequest(BaseModel):
    segments: list[BulkRateSegment] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_total_span(self) -> "BulkRatesPutRequest":
        total = 0
        for seg in self.segments:
            total += (seg.end_date - seg.start_date).days + 1
        if total > 366:
            raise ValueError(
                "total number of nightly rate rows in one request cannot exceed 366",
            )
        return self

    model_config = ConfigDict(extra="forbid")


class BulkRatesPutResponse(BaseModel):
    rows_upserted: int
