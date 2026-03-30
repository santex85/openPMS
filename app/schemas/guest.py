"""Pydantic models for guest CRUD API."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GuestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    first_name: str
    last_name: str
    email: str
    phone: str
    passport_data: str | None = None
    nationality: str | None = None
    date_of_birth: date | None = None
    notes: str | None = None
    vip_status: bool
    created_at: datetime
    updated_at: datetime


class GuestListPage(BaseModel):
    """Paginated guest list (GET /guests)."""

    items: list[GuestRead]
    total: int
    limit: int
    offset: int


class GuestCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=255)
    last_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=320)
    phone: str = Field(..., min_length=1, max_length=64)
    passport_data: str | None = Field(None, max_length=255)
    nationality: str | None = Field(None, min_length=2, max_length=2)
    date_of_birth: date | None = None
    notes: str | None = None
    vip_status: bool = False

    @field_validator("nationality", mode="before")
    @classmethod
    def uppercase_nationality(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).strip().upper()
        if len(s) != 2 or not s.isalpha():
            raise ValueError("nationality must be ISO 3166-1 alpha-2")
        return s

    model_config = ConfigDict(extra="forbid")


class GuestPatch(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=255)
    last_name: str | None = Field(None, min_length=1, max_length=255)
    email: str | None = Field(None, min_length=1, max_length=320)
    phone: str | None = Field(None, min_length=1, max_length=64)
    passport_data: str | None = Field(None, max_length=255)
    nationality: str | None = Field(None, min_length=2, max_length=2)
    date_of_birth: date | None = None
    notes: str | None = None
    vip_status: bool | None = None

    @field_validator("nationality", mode="before")
    @classmethod
    def uppercase_nationality_patch(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).strip().upper()
        if len(s) != 2 or not s.isalpha():
            raise ValueError("nationality must be ISO 3166-1 alpha-2")
        return s

    model_config = ConfigDict(extra="forbid")


class GuestBookingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    status: str
    source: str
    total_amount: Decimal
    check_in_date: date | None = None
    check_out_date: date | None = None


class GuestDetailRead(GuestRead):
    bookings: list[GuestBookingSummary] = Field(default_factory=list)
