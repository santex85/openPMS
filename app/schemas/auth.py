"""Pydantic models for auth-related API requests and responses."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UnauthorizedResponse(BaseModel):
    detail: str = Field(
        ...,
        description="Human-readable reason for authentication failure.",
    )


class AuthRegisterRequest(BaseModel):
    tenant_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)
    full_name: str = Field(..., min_length=1, max_length=255)
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "tenant_name": "Demo Resort Co",
                "email": "owner@example.com",
                "password": "change-me-please",
                "full_name": "Owner User",
            }
        },
    )


class AuthLoginRequest(BaseModel):
    tenant_id: UUID | None = Field(
        default=None,
        description=(
            "Tenant scope. Omit when this email matches exactly one active user; "
            "pass it when the same email exists in multiple organizations."
        ),
    )
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "tenant_id": None,
                "email": "owner@example.com",
                "password": "change-me-please",
            }
        },
    )

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _empty_string_tenant_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v


class AuthLogoutRequest(BaseModel):
    tenant_id: UUID | None = Field(
        default=None,
        description=(
            "Tenant scope for revoking refresh (RLS). Required with token to update DB rows."
        ),
    )
    refresh_token: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "Send in body optionally; alternatively use HttpOnly cookie from auth endpoints."
        ),
    )
    model_config = ConfigDict(extra="forbid")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _empty_string_tenant_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v


class AuthRefreshRequest(BaseModel):
    tenant_id: UUID
    refresh_token: str | None = Field(
        default=None,
        max_length=4096,
        description="Omit when using HttpOnly cookie set by POST /auth/login or /auth/refresh.",
    )
    model_config = ConfigDict(extra="forbid")


class AuthInviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=32)
    model_config = ConfigDict(extra="forbid")


class AuthChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)
    model_config = ConfigDict(extra="forbid")


class UserPatchRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = Field(None, min_length=1, max_length=32)
    model_config = ConfigDict(extra="forbid")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    """Returned by /auth/refresh; new refresh is only in HttpOnly cookie."""

    access_token: str
    token_type: str = "bearer"


class AuthRegisterResponse(TokenPairResponse):
    tenant_id: UUID
    user: UserRead


class AuthRegisterPublicResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: UUID
    user: UserRead


class AuthLoginResponse(TokenPairResponse):
    user: UserRead


class AuthLoginPublicResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class AuthInviteResponse(BaseModel):
    user: UserRead
    temporary_password: str = Field(
        ...,
        description=(
            "Deprecated: the invitee receives the temporary password by email. "
            "Kept for backward compatibility; do not persist in client logs."
        ),
        deprecated=True,
    )
