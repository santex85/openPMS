"""Housekeeping board and patch API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HousekeepingRoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    room_type_id: UUID
    room_type_name: str
    name: str
    status: str
    housekeeping_status: str
    housekeeping_priority: str


class HousekeepingPatchRequest(BaseModel):
    housekeeping_status: str = Field(..., min_length=1, max_length=32)
    housekeeping_priority: str | None = Field(None, min_length=1, max_length=32)

    model_config = ConfigDict(extra="forbid")


class HousekeepingPatchResponse(BaseModel):
    id: UUID
    housekeeping_status: str
    housekeeping_priority: str
    model_config = ConfigDict(extra="forbid")

