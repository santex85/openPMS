"""Application settings loaded from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    jwt_secret: str = Field(
        default="",
        description="HS256 shared secret (min 32 chars). Unused when jwt_algorithm is RS256.",
    )
    jwt_algorithm: str = "HS256"
    jwt_private_key_pem: str | None = Field(
        default=None,
        description="PEM RSA private key for RS256 signing (production).",
    )
    jwt_public_key_pem: str | None = Field(
        default=None,
        description="PEM RSA public key for RS256 verify (optional if private key is set).",
    )
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    access_token_ttl_minutes: int = Field(
        default=60,
        ge=1,
        description="Lifetime of access JWT (minutes).",
    )
    refresh_token_ttl_days: int = Field(
        default=14,
        ge=1,
        description="Lifetime of refresh tokens (days).",
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated browser origins allowed for CORS (e.g. Vite dev server).",
    )
    refresh_cookie_name: str = Field(
        default="openpms_refresh",
        description="HttpOnly cookie name for browser refresh token rotation.",
    )
    refresh_cookie_secure: bool = Field(
        default=False,
        description="Set Secure flag on refresh cookie (enable in production over HTTPS).",
    )
    webhook_secret_fernet_key: str | None = Field(
        default=None,
        description=(
            "URL-safe base64 Fernet key for encrypting webhook subscription secrets at rest. "
            "If unset, a key is derived from jwt_secret (rotate jwt_secret invalidates derived keys)."
        ),
    )

    @model_validator(mode="after")
    def validate_jwt_config(self) -> Self:
        alg = self.jwt_algorithm.upper()
        if alg == "HS256":
            if len(self.jwt_secret) < 32:
                msg = "jwt_secret must be at least 32 characters for HS256"
                raise ValueError(msg)
        elif alg == "RS256":
            if (
                self.jwt_private_key_pem is None
                or not str(self.jwt_private_key_pem).strip()
            ):
                msg = "jwt_private_key_pem is required when jwt_algorithm is RS256"
                raise ValueError(msg)
        else:
            raise ValueError(f"Unsupported jwt_algorithm: {self.jwt_algorithm}")
        return self

    def cors_allowed_origins(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
