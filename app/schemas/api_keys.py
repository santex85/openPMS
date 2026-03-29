"""API key CRUD schemas (plaintext secret only on create)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] = Field(..., min_length=1)
    expires_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None


class ApiKeyCreateResponse(ApiKeyRead):
    """Plaintext key is returned only in this response."""

    key: str = Field(description="Store securely; not shown again.")


class ApiKeyPatchRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")
