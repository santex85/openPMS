"""Pydantic shapes for Channex API JSON (minimal; extend as integration grows)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChannexProperty(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class ChannexRoomType(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class ChannexRatePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None


class ChannexBookingRevisionPayload(BaseModel):
    """Subset of booking revision fields used after GET /booking_revisions/{id}."""

    model_config = ConfigDict(extra="ignore")

    id: str
    booking_id: str | None = None
    status: str | None = None
