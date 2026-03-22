"""Pydantic models for bookings API."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.stay_dates import MAX_STAY_NIGHTS


class GuestPayload(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=255)
    last_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=320)
    phone: str = Field(..., min_length=1, max_length=64)
    passport_data: str | None = Field(None, max_length=255)


class BookingCreateRequest(BaseModel):
    property_id: UUID
    room_type_id: UUID
    rate_plan_id: UUID
    check_in: date
    check_out: date
    guest: GuestPayload
    status: str = Field(default="confirmed", max_length=64)
    source: str = Field(default="api", max_length=64)

    @model_validator(mode="after")
    def validate_stay_dates(self) -> "BookingCreateRequest":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if (self.check_out - self.check_in).days > MAX_STAY_NIGHTS:
            raise ValueError(f"stay cannot exceed {MAX_STAY_NIGHTS} nights")
        return self

    model_config = ConfigDict(extra="forbid")


class NightlyPriceLine(BaseModel):
    date: date
    price: Decimal


class BookingCreateResponse(BaseModel):
    booking_id: UUID
    guest_id: UUID
    total_amount: Decimal
    nights: list[NightlyPriceLine]
