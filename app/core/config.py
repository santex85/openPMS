"""Application settings loaded from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256: block well-known dev / example secrets at startup (see lifespan).
KNOWN_WEAK_JWT_SECRETS_HS256: frozenset[str] = frozenset(
    {
        # Former docker-compose default (removed); must not be used in any environment.
        "openpms-dev-jwt-secret-min-32-chars!!",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    db_pool_size: int = Field(
        default=10,
        ge=1,
        description="SQLAlchemy async engine pool size.",
    )
    db_max_overflow: int = Field(
        default=5,
        ge=0,
        description="Max overflow connections beyond pool_size.",
    )
    db_pool_timeout: float = Field(
        default=30.0,
        ge=1,
        description="Seconds to wait for a connection from the pool.",
    )
    db_pool_recycle: int = Field(
        default=1800,
        ge=300,
        description="Recycle connections after this many seconds (server-side timeouts).",
    )
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
        default=True,
        description="Set Secure flag on refresh cookie (disable only for local HTTP dev).",
    )
    webhook_secret_fernet_key: str | None = Field(
        default=None,
        description=(
            "URL-safe base64 Fernet key for encrypting webhook subscription secrets at rest. "
            "If unset, a key is derived from jwt_secret (rotate jwt_secret invalidates derived keys)."
        ),
    )
    webhook_log_retention_days: int = Field(
        default=30,
        ge=1,
        description="Delete webhook_delivery_logs rows older than this many days (retention job / CLI).",
    )
    allow_public_registration: bool = Field(
        default=False,
        description=(
            "When false, POST /auth/register returns 403; use invite flow (/auth/invite) instead."
        ),
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        description="Celery broker URL (Redis).",
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

    @model_validator(mode="after")
    def validate_webhook_fernet_for_rs256(self) -> Self:
        if self.jwt_algorithm.upper() == "RS256" and not (
            self.webhook_secret_fernet_key or ""
        ).strip():
            msg = (
                "webhook_secret_fernet_key is required when jwt_algorithm is RS256 "
                "(Fernet key cannot be derived from jwt_secret in RS256 mode)"
            )
            raise ValueError(msg)
        return self

    def cors_allowed_origins(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_jwt_secret_not_weak(settings: Settings) -> None:
    """Refuse startup when HS256 secret matches a known default (predictable) value."""
    if settings.jwt_algorithm.upper() != "HS256":
        return
    if settings.jwt_secret in KNOWN_WEAK_JWT_SECRETS_HS256:
        msg = (
            "JWT_SECRET must not use a known weak/default value. "
            "Generate a strong secret (see scripts/generate-secrets.sh)."
        )
        raise ValueError(msg)


def clear_settings_cache() -> None:
    """Drop cached Settings (e.g. between tests after changing os.environ)."""
    get_settings.cache_clear()
