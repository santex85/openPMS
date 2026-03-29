"""Pydantic models for auth-related API requests and responses."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    model_config = ConfigDict(extra="forbid")


class AuthLoginRequest(BaseModel):
    tenant_id: UUID
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)
    model_config = ConfigDict(extra="forbid")


class AuthRefreshRequest(BaseModel):
    tenant_id: UUID
    refresh_token: str = Field(..., min_length=10, max_length=4096)
    model_config = ConfigDict(extra="forbid")


class AuthInviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=32)
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


class AuthRegisterResponse(TokenPairResponse):
    tenant_id: UUID
    user: UserRead


class AuthLoginResponse(TokenPairResponse):
    user: UserRead


class AuthInviteResponse(BaseModel):
    user: UserRead
    temporary_password: str = Field(
        ...,
        description="Shown once; store securely on the client if needed.",
    )
