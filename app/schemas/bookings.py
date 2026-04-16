"""Pydantic models for bookings API."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.stay_dates import MAX_STAY_NIGHTS


class GuestPayload(BaseModel):
    first_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Guest given name.",
        examples=["Somchai"],
    )
    last_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Guest family name.",
        examples=["Dechathon"],
    )
    email: str = Field(
        ...,
        min_length=1,
        max_length=320,
        description="Normalized to lower-case for deduplication per tenant.",
        examples=["guest@example.com"],
    )
    phone: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Contact phone (E.164 recommended).",
        examples=["+66812345678"],
    )
    passport_data: str | None = Field(
        None,
        max_length=255,
        description="Optional passport or ID note.",
    )


class BookingCreateRequest(BaseModel):
    property_id: UUID = Field(description="Property the stay belongs to.")
    room_type_id: UUID = Field(description="Booked room category.")
    rate_plan_id: UUID = Field(description="Pricing plan (BAR, package, etc.).")
    check_in: date = Field(description="First night of stay (date of occupancy).")
    check_out: date = Field(
        description="Morning of departure (exclusive end, like hotel PMS).",
    )
    guest: GuestPayload
    status: Literal["pending", "confirmed"] = Field(
        default="confirmed",
        description="Initial booking status (lifecycle starts pending or confirmed).",
    )
    source: str = Field(
        default="api",
        max_length=64,
        description="Booking channel or origin.",
        examples=["api", "direct"],
    )
    force_new_guest: bool = Field(
        default=False,
        description="If true, always create a new guest row (no dedupe by email).",
    )

    @model_validator(mode="after")
    def validate_stay_dates(self) -> "BookingCreateRequest":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if (self.check_out - self.check_in).days > MAX_STAY_NIGHTS:
            raise ValueError(f"stay cannot exceed {MAX_STAY_NIGHTS} nights")
        return self

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "property_id": "550e8400-e29b-41d4-a716-446655440001",
                "room_type_id": "550e8400-e29b-41d4-a716-446655440002",
                "rate_plan_id": "550e8400-e29b-41d4-a716-446655440003",
                "check_in": "2026-05-01",
                "check_out": "2026-05-04",
                "guest": {
                    "first_name": "Somchai",
                    "last_name": "Jaidee",
                    "email": "somchai@example.com",
                    "phone": "+66812345678",
                },
                "status": "confirmed",
                "source": "api",
                "force_new_guest": False,
            }
        },
    )


class NightlyPriceLine(BaseModel):
    date: date
    price: Decimal


class BookingCreateResponse(BaseModel):
    booking_id: UUID
    guest_id: UUID
    total_amount: Decimal
    nights: list[NightlyPriceLine]
    guest_merged: bool = Field(
        default=False,
        description="True when an existing guest was matched by email instead of creating a new profile.",
    )


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    guest_id: UUID
    status: str
    source: str
    total_amount: Decimal


class GuestTapeRead(BaseModel):
    """Guest summary for board / tape list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str


class BookingPatchRequest(BaseModel):
    """Partial booking update: room assignment, status, or stay dates."""

    room_id: UUID | None = Field(
        None,
        description="Assign or clear physical room for all booking nights.",
    )
    status: str | None = Field(
        None,
        max_length=64,
        description="Booking lifecycle status (e.g. confirmed, checked_in, checked_out, cancelled).",
    )
    check_in: date | None = Field(
        None,
        description="New stay start (night); triggers repricing when combined with check_out.",
    )
    check_out: date | None = Field(
        None,
        description="New stay end (exclusive); triggers repricing when combined with check_in.",
    )
    cancellation_reason: str | None = Field(
        None,
        max_length=512,
        description="Optional reason when cancelling; included in webhook booking.cancelled.",
    )

    model_config = ConfigDict(extra="forbid")


class BookingTapeRead(BaseModel):
    """Booking row for availability board: stay bounds from lines, guest summary."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    guest_id: UUID
    status: str
    source: str
    total_amount: Decimal
    guest: GuestTapeRead
    check_in_date: date | None = None
    check_out_date: date | None = None
    room_id: UUID | None = None
    room_type_id: UUID | None = None


class BookingTapePage(BaseModel):
    """Paginated bookings tape (GET /bookings)."""

    items: list[BookingTapeRead]
    total: int
    limit: int
    offset: int


class BookingUnpaidFolioSummaryRead(BaseModel):
    """Row for GET /bookings/unpaid-folio-summary (positive folio balance)."""

    booking_id: UUID
    balance: str = Field(description="Decimal string; positive means guest owes.")
    guest_name: str | None = Field(
        None,
        description="Guest full name for dashboard display.",
    )


class SendInvoiceRequest(BaseModel):
    """Optional body for POST /bookings/{id}/send-invoice."""

    email: str | None = Field(
        None,
        max_length=320,
        description="Recipient override; defaults to guest email on the booking.",
    )

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_body(cls, raw: dict[str, Any]) -> "SendInvoiceRequest":
        return cls.model_validate(raw or {})
