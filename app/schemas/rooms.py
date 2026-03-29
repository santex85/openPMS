"""Pydantic models for rooms API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoomRead(BaseModel):
    """Physical room returned by CRUD and list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_type_id: UUID
    name: str
    status: str


class RoomListRead(BaseModel):
    """Room row for board grid (stable id per physical room)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_type_id: UUID
    name: str
    status: str


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
