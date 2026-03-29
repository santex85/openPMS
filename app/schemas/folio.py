"""Pydantic models for booking folio (charges, payments, balance)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

FolioCategoryLiteral = Literal[
    "room_charge",
    "food_beverage",
    "spa",
    "minibar",
    "tax",
    "discount",
    "payment",
]

CHARGE_CATEGORIES: frozenset[str] = frozenset(
    {
        "room_charge",
        "food_beverage",
        "spa",
        "minibar",
        "tax",
        "discount",
    },
)


class FolioTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    booking_id: UUID
    transaction_type: str
    amount: Decimal
    payment_method: str | None
    description: str | None
    created_at: datetime
    created_by: UUID | None
    category: str

    @field_serializer("amount")
    def serialize_decimal(self, v: Decimal) -> str:
        return format(v, "f")


class FolioListResponse(BaseModel):
    transactions: list[FolioTransactionRead] = Field(
        description="Ledger rows in chronological order.",
    )
    balance: Decimal = Field(
        description="Positive: guest owes; negative: overpay (charges minus payments).",
    )

    @field_serializer("balance")
    def serialize_balance(self, v: Decimal) -> str:
        return format(v, "f")


class FolioPostRequest(BaseModel):
    entry_type: Literal["charge", "payment"] = Field(
        description="charge posts to receivable; payment reduces balance.",
        examples=["charge"],
    )
    amount: Decimal = Field(
        ...,
        gt=0,
        description="Absolute amount; discount category stored as negative charge.",
        examples=["25.00"],
    )
    category: FolioCategoryLiteral = Field(
        description="Line category (payment must use category payment).",
    )
    description: str | None = Field(
        None,
        max_length=512,
        description="Optional memo on the folio line.",
    )
    payment_method: str | None = Field(
        None,
        max_length=64,
        description="Required for payments (e.g. cash, card).",
        examples=["card"],
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_payment_category(self) -> "FolioPostRequest":
        if self.entry_type == "payment" and self.category != "payment":
            raise ValueError('payment entries must use category "payment"')
        if self.entry_type == "charge" and self.category == "payment":
            raise ValueError('charge entries cannot use category "payment"')
        if self.entry_type == "payment" and not (
            self.payment_method and self.payment_method.strip()
        ):
            raise ValueError("payment_method is required for payment entries")
        return self


class BookingCheckoutBalanceWarning(BaseModel):
    """Returned on PATCH booking when transitioning to checked_out with non-zero folio balance."""

    folio_balance_warning: bool = True
    balance: Decimal

    @field_serializer("balance")
    def serialize_balance(self, v: Decimal) -> str:
        return format(v, "f")
