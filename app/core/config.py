"""Application settings loaded from environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
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

    def cors_allowed_origins(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
