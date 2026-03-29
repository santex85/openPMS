"""Pydantic models for rate plan API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RatePlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    name: str
    cancellation_policy: str


class RatePlanCreate(BaseModel):
    property_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    cancellation_policy: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class RatePlanPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    cancellation_policy: str | None = Field(None, min_length=1)
    model_config = ConfigDict(extra="forbid")
