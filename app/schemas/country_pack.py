"""Request/response models for country packs, extensions, and tax preview."""

from datetime import datetime, time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaxRuleSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=100)
    rate: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    inclusive: bool
    applies_to: list[str] = Field(..., min_length=1)
    compound_after: str | None = Field(None, max_length=32)
    display_on_folio: bool = True
    active: bool = True


class CountryPackListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    currency_code: str
    is_builtin: bool


class CountryPackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    tenant_id: UUID | None
    name: str
    currency_code: str
    currency_symbol: str
    currency_symbol_position: Literal["before", "after"]
    currency_decimal_places: int
    timezone: str
    date_format: str
    locale: str
    default_checkin_time: time
    default_checkout_time: time
    taxes: list[TaxRuleSchema]
    payment_methods: list[str]
    fiscal_year_start: str | None
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("taxes", mode="before")
    @classmethod
    def coerce_taxes(cls, v: object) -> object:
        if v is None:
            return []
        return v

    @field_validator("payment_methods", mode="before")
    @classmethod
    def coerce_pm(cls, v: object) -> object:
        if v is None:
            return []
        return v


class CountryPackCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=100)
    currency_code: str = Field(..., min_length=3, max_length=3)
    currency_symbol: str = Field(..., min_length=1, max_length=8)
    currency_symbol_position: Literal["before", "after"]
    currency_decimal_places: int = Field(..., ge=0, le=4)
    timezone: str = Field(..., min_length=1, max_length=64)
    date_format: str = Field(..., min_length=1, max_length=20)
    locale: str = Field(..., min_length=1, max_length=10)
    default_checkin_time: time
    default_checkout_time: time
    taxes: list[TaxRuleSchema] = Field(default_factory=list)
    payment_methods: list[str] = Field(default_factory=list)
    fiscal_year_start: str | None = Field(None, max_length=5)

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency_code")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.strip().upper()


class CountryPackPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    currency_symbol: str | None = Field(None, min_length=1, max_length=8)
    currency_symbol_position: Literal["before", "after"] | None = None
    currency_decimal_places: int | None = Field(None, ge=0, le=4)
    timezone: str | None = Field(None, min_length=1, max_length=64)
    date_format: str | None = Field(None, min_length=1, max_length=20)
    locale: str | None = Field(None, min_length=1, max_length=10)
    default_checkin_time: time | None = None
    default_checkout_time: time | None = None
    taxes: list[TaxRuleSchema] | None = None
    payment_methods: list[str] | None = None
    fiscal_year_start: str | None = Field(None, max_length=5)

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency_code")
    @classmethod
    def currency_upper(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()


class CountryPackApplyRequest(BaseModel):
    property_id: UUID

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"property_id": "550e8400-e29b-41d4-a716-446655440001"}
        },
    )


class CountryPackApplyResponse(BaseModel):
    property_id: UUID
    country_pack_code: str
    currency: str
    timezone: str
    checkin_time: time
    checkout_time: time
    payment_methods: list[str]


class PropertyLockStatusRead(BaseModel):
    property_id: UUID
    country_pack_locked: bool
    booking_count: int


class ExtensionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    webhook_url: str = Field(..., min_length=1)
    required_fields: list[str] = Field(default_factory=list)
    ui_config_schema: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("country_code")
    @classmethod
    def country_upper(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()


class ExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    code: str
    name: str
    country_code: str | None
    webhook_url: str
    required_fields: list[str]
    ui_config_schema: dict[str, Any] | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("required_fields", mode="before")
    @classmethod
    def coerce_rf(cls, v: object) -> object:
        if v is None:
            return []
        return v


class PropertyExtensionUpsert(BaseModel):
    property_id: UUID
    extension_id: UUID
    config: dict[str, Any] | None = None
    is_active: bool = True

    model_config = ConfigDict(extra="forbid")


class PropertyExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    extension_id: UUID
    config: dict[str, Any] | None
    is_active: bool
    created_at: datetime


class TaxLineResponse(BaseModel):
    code: str
    name: str
    amount: Decimal

    model_config = ConfigDict(extra="forbid")


class TaxCalculationResponse(BaseModel):
    lines: list[TaxLineResponse]
    subtotal: Decimal
    total_with_taxes: Decimal

    model_config = ConfigDict(extra="forbid")
