"""Pydantic models for room types API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoomTypeCreate(BaseModel):
    property_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    base_occupancy: int = Field(..., ge=1)
    max_occupancy: int = Field(..., ge=1)

    @model_validator(mode="after")
    def max_gte_base(self) -> "RoomTypeCreate":
        if self.max_occupancy < self.base_occupancy:
            raise ValueError("max_occupancy must be >= base_occupancy")
        return self


class RoomTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    name: str
    base_occupancy: int
    max_occupancy: int


class RoomTypePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    base_occupancy: int | None = Field(None, ge=1)
    max_occupancy: int | None = Field(None, ge=1)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def base_le_max_when_both_set(self) -> "RoomTypePatch":
        if self.base_occupancy is not None and self.max_occupancy is not None:
            if self.max_occupancy < self.base_occupancy:
                raise ValueError("max_occupancy must be >= base_occupancy")
        return self
