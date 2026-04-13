"""Channex integration API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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
    channex_webhook_id: str | None = None
    channex_env: str
    status: str
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


class ChannexSyncQueuedResponse(BaseModel):
    """Return body for POST .../channex/sync when ARI job is enqueued."""

    detail: str = "Sync queued"


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


class ChannexProvisionRead(BaseModel):
    """Counts after creating missing Channex room types and rate plans from OpenPMS."""

    room_types_created: int = 0
    room_types_skipped: int = 0
    rate_plans_created: int = 0
    rate_plans_skipped: int = 0


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


class ChannexRevisionFailedRead(BaseModel):
    """One Channex booking revision row stuck in ``error`` (OpenPMS DB row)."""

    id: UUID = Field(
        description="OpenPMS ``channex_booking_revisions.id`` (not Channex revision id)."
    )
    channex_revision_id: str
    channex_booking_id: str | None = None
    property_id: UUID
    channel_code: str | None = None
    error_message: str | None = None
    received_at: datetime
    processed_at: datetime | None = None


class ChannexRevisionsFailedListResponse(BaseModel):
    total: int
    items: list[ChannexRevisionFailedRead]


class ChannexRevisionRetryQueuedResponse(BaseModel):
    status: Literal["queued"] = "queued"
