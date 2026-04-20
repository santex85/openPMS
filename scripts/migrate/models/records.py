"""Normalized migration records (internal format between adapters and pipeline)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["error", "warning"] = "error"
    message: str
    source_ref: str | None = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)


class GuestRecord(BaseModel):
    """Guest row from a source system (e.g. Preno export)."""

    model_config = ConfigDict(extra="ignore")

    external_id: str = Field(..., description="Stable id in source PMS")
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    nationality: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2 when known",
    )
    notes: str | None = None
    vip_status: bool = Field(
        default=False,
        description="True when blacklisted / VIP flag per import rules",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        return str(v).strip().lower()

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        raw = str(v).strip()
        digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
        if not digits:
            return None
        if digits.startswith("+"):
            return digits
        return digits


class BookingGuestSnapshot(BaseModel):
    """Primary guest on a booking (subset of GuestPayload / GuestCreate)."""

    model_config = ConfigDict(extra="forbid")

    first_name: str
    last_name: str
    email: str
    phone: str
    passport_data: str | None = None


class BookingRecord(BaseModel):
    """Booking row normalized for OpenPMS import."""

    model_config = ConfigDict(extra="ignore")

    external_id: str
    check_in: date
    check_out: date
    room_type_name: str
    rate_plan_name: str
    guest: BookingGuestSnapshot
    status: Literal[
        "pending",
        "confirmed",
        "checked_in",
        "checked_out",
        "cancelled",
        "no_show",
    ]
    source: str = "migration"
    notes: str | None = None
    adults: int | None = None
    total_source: Decimal | None = Field(
        default=None,
        description="Total from source CSV (reporting only)",
    )


class RoomTypeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    base_occupancy: int = 2
    max_occupancy: int = 4


class RoomRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_type_name: str
    name: str
    status: str = "available"


class RatePlanRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cancellation_policy: str = "standard"
