"""Booking receipt (folio charges + optional property tax)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tax_config import TaxBreakdown, TaxModeLiteral


class BookingReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    booking_id: UUID
    property_id: UUID
    guest_first_name: str | None = None
    guest_last_name: str | None = None
    currency: str | None = Field(
        None,
        description="Property currency when available.",
    )
    charge_subtotal: str = Field(
        ...,
        description="Sum of posted folio charges (basis for property tax).",
    )
    tax_mode: TaxModeLiteral | None = None
    tax_name: str | None = None
    tax_rate: str | None = None
    tax_breakdown: TaxBreakdown | None = None
    tax_summary_lines: list[str] = Field(default_factory=list)
