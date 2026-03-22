"""Pydantic models for auth-related API responses."""

from pydantic import BaseModel, Field


class UnauthorizedResponse(BaseModel):
    detail: str = Field(
        ...,
        description="Human-readable reason for authentication failure.",
    )
