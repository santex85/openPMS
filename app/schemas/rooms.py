"""Pydantic models for rooms API."""

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.stay_dates import iter_stay_nights


class RoomRead(BaseModel):
    """Physical room returned by CRUD and list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_type_id: UUID
    name: str
    status: str
    housekeeping_status: str
    housekeeping_priority: str


class RoomCreate(BaseModel):
    room_type_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="available", max_length=64)
    model_config = ConfigDict(extra="forbid")


class RoomPatch(BaseModel):
    room_type_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(None, max_length=64)
    model_config = ConfigDict(extra="forbid")


class AssignableRoomsQueryParams(BaseModel):
    """Query string for GET /rooms/assignable (check-out is exclusive)."""

    property_id: UUID
    room_type_id: UUID
    check_in: date = Field(description="First night (inclusive)")
    check_out: date = Field(description="Last night excluded (exclusive checkout)")

    @model_validator(mode="after")
    def validate_stay(self) -> Self:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        iter_stay_nights(self.check_in, self.check_out)
        return self
