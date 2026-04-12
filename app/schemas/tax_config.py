"""Pydantic models for per-property tax configuration (Phase 1)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

TaxModeLiteral = Literal["off", "inclusive", "exclusive"]


class TaxBreakdown(BaseModel):
    """Single-rate property tax breakdown (amount semantics depend on tax_mode)."""

    model_config = ConfigDict(from_attributes=False)

    tax_amount: Decimal
    gross_total: Decimal
    net_total: Decimal

    @field_serializer("tax_amount", "gross_total", "net_total")
    def _dec(self, v: Decimal) -> str:
        return format(v, "f")


class TaxConfigCreate(BaseModel):
    tax_mode: TaxModeLiteral
    tax_name: str = Field(..., min_length=1, max_length=255)
    tax_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Rate as fraction, e.g. 0.07 for 7%.",
    )

    model_config = ConfigDict(json_schema_extra={"examples": [{"tax_mode": "inclusive", "tax_name": "VAT", "tax_rate": "0.07"}]})


class TaxConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    tax_mode: TaxModeLiteral
    tax_name: str
    tax_rate: Decimal
    created_at: datetime
    updated_at: datetime

    @field_serializer("tax_rate")
    def _rate(self, v: Decimal) -> str:
        return format(v, "f")
