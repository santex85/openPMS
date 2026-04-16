"""API schemas for per-property email settings (TZ-16)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmailSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    sender_name: str
    reply_to: str | None
    logo_url: str | None
    locale: str
    created_at: datetime
    updated_at: datetime


class EmailSettingsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_name: str = Field(..., min_length=1, max_length=255)
    reply_to: str | None = Field(None, max_length=320)
    logo_url: str | None = Field(None, max_length=8000)
    locale: str = Field(..., min_length=2, max_length=16)
