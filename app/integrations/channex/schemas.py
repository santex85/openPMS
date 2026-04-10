"""Pydantic shapes for Channex API JSON (minimal; extend as integration grows)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChannexRevisionCustomer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    surname: str | None = None
    mail: str | None = None
    phone: str | None = None


class ChannexRevisionRoom(BaseModel):
    model_config = ConfigDict(extra="ignore")

    room_type_id: str | None = None
    rate_plan_id: str | None = None
    checkin_date: str | None = None
    checkout_date: str | None = None


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
    """Fields from GET /booking_revisions/{id} (flat attributes JSON:API style)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    booking_id: str | None = None
    property_id: str | None = None
    status: str | None = None
    arrival_date: str | None = None
    departure_date: str | None = None
    amount: str | float | int | None = None
    currency: str | None = None
    customer: ChannexRevisionCustomer | None = None
    rooms: list[ChannexRevisionRoom] = Field(default_factory=list)
    occupancy: dict[str, Any] | None = None
    notes: str | None = None
    channel_id: str | None = None
