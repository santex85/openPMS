"""Pydantic models for properties API."""

import re
from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PropertyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    timezone: str = Field(..., min_length=1, max_length=64)
    currency: str = Field(..., min_length=3, max_length=3)
    checkin_time: time
    checkout_time: time
    country_pack_code: str | None = Field(
        None,
        max_length=32,
        description="Optional FK to country_packs.code (apply builtin/custom preset).",
    )

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        u = v.strip().upper()
        if not _CURRENCY_RE.match(u):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return u

    @field_validator("timezone")
    @classmethod
    def timezone_iana(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("timezone must not be empty")
        try:
            ZoneInfo(s)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA time zone name") from exc
        return s


class PropertyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    timezone: str | None = Field(None, min_length=1, max_length=64)
    currency: str | None = Field(None, min_length=3, max_length=3)
    checkin_time: time | None = None
    checkout_time: time | None = None
    country_pack_code: str | None = Field(None, max_length=32)

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency")
    @classmethod
    def currency_upper_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        u = v.strip().upper()
        if not _CURRENCY_RE.match(u):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return u

    @field_validator("timezone")
    @classmethod
    def timezone_iana_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("timezone must not be empty")
        try:
            ZoneInfo(s)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA time zone name") from exc
        return s


class PropertyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    country_pack_code: str | None = None
    timezone: str
    currency: str
    checkin_time: time
    checkout_time: time
