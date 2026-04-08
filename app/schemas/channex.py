"""Channex integration API schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChannexValidateKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    env: str = Field(default="production")


class ChannexConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    env: str = Field(default="production")
    channex_property_id: str = Field(..., min_length=1)


class ChannexPropertyRead(BaseModel):
    """Channex property row returned after key validation."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class ChannexPropertyLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    channex_property_id: str
    channex_env: str
    status: str
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


class ChannexRoomTypeMapRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_type_id: UUID
    channex_room_type_id: str
    channex_room_type_name: str | None = None


class ChannexStatusRead(BaseModel):
    connected: bool
    link: ChannexPropertyLinkRead | None = None
    room_maps_count: int = 0
    rate_maps_count: int = 0
    room_type_maps: list[ChannexRoomTypeMapRead] = Field(default_factory=list)


class ChannexRoomTypeRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class ChannexRatePlanRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class RoomMappingItem(BaseModel):
    room_type_id: UUID
    channex_room_type_id: str = Field(..., min_length=1)
    channex_room_type_name: str | None = None


class RoomMappingRequest(BaseModel):
    mappings: list[RoomMappingItem]


class RateMappingItem(BaseModel):
    room_type_map_id: UUID
    rate_plan_id: UUID
    channex_rate_plan_id: str = Field(..., min_length=1)
    channex_rate_plan_name: str | None = None


class RateMappingRequest(BaseModel):
    mappings: list[RateMappingItem]
