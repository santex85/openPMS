"""Schemas for per-tenant folio charge category catalog."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class FolioChargeCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    code: str
    label: str
    is_builtin: bool
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class FolioChargeCategoryCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    label: str = Field(..., min_length=1, max_length=64)
    sort_order: int = Field(default=0)
    is_active: bool = Field(default=True)

    model_config = ConfigDict(extra="forbid")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        c = v.strip()
        if not _CODE_PATTERN.fullmatch(c):
            raise ValueError(
                "code must match ^[a-z][a-z0-9_]{0,31}$",
            )
        if c == "payment":
            raise ValueError('code "payment" is reserved for payment entries')
        return c

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        t = v.strip()
        if t == "":
            raise ValueError("label must not be empty")
        return t


class FolioChargeCategoryUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=64)
    sort_order: int | None = None
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        t = v.strip()
        if t == "":
            raise ValueError("label must not be empty")
        return t
