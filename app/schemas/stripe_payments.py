"""Stripe Payments API schemas (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class SavePaymentMethodRequest(BaseModel):
    stripe_pm_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Stripe PaymentMethod id (pm_...).",
    )
    booking_id: UUID | None = None
    label: str | None = Field(None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    booking_id: UUID | None
    stripe_pm_id: str
    card_last4: str | None
    card_brand: str | None
    card_exp_month: int | None
    card_exp_year: int | None
    label: str | None
    created_at: datetime


class ChargeRequest(BaseModel):
    """Charge body: ``stripe_pm_id`` is the internal saved row id (not pm_...)."""

    stripe_pm_id: UUID = Field(
        ...,
        description="UUID of a row in stripe_payment_methods for this property.",
    )
    amount: Decimal = Field(..., gt=0, description="Major currency units (e.g. USD).")
    label: str | None = Field(None, max_length=512)

    model_config = ConfigDict(extra="forbid")

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal) -> str:
        return format(v.quantize(Decimal("0.01")), "f")


class RefundRequest(BaseModel):
    """Refund body: ``stripe_charge_id`` is the internal stripe_charges row id (not ch_...)."""

    stripe_charge_id: UUID = Field(
        ...,
        description="UUID of a row in stripe_charges for this booking.",
    )
    amount: Decimal | None = Field(
        None,
        description="Partial refund amount; omit for full refund.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal | None) -> str | None:
        if v is None:
            return None
        return format(v.quantize(Decimal("0.01")), "f")


class ChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    booking_id: UUID | None
    folio_tx_id: UUID | None
    stripe_charge_id: str
    stripe_pm_id: str | None
    amount: Decimal
    currency: str
    status: str
    failure_message: str | None
    created_at: datetime

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal) -> str:
        return format(v, "f")
