"""Channex API key Fernet encrypt/decrypt."""

from __future__ import annotations

from cryptography.fernet import Fernet

import pytest

from app.core.config import Settings
from app.integrations.channex.crypto import (
    decrypt_channex_api_key,
    encrypt_channex_api_key,
)


@pytest.fixture
def settings_with_fernet() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://openpms:openpms@localhost:5432/openpms",
        jwt_secret="x" * 32,
        webhook_secret_fernet_key=Fernet.generate_key().decode("ascii"),
    )


def test_encrypt_decrypt_roundtrip(settings_with_fernet: Settings) -> None:
    plain = "channex-user-api-key-abc"
    enc = encrypt_channex_api_key(settings_with_fernet, plain)
    assert enc != plain
    assert decrypt_channex_api_key(settings_with_fernet, enc) == plain


def test_decrypt_invalid_token_raises(settings_with_fernet: Settings) -> None:
    other = Settings(
        database_url=settings_with_fernet.database_url,
        jwt_secret="y" * 32,
        webhook_secret_fernet_key=Fernet.generate_key().decode("ascii"),
    )
    enc = encrypt_channex_api_key(settings_with_fernet, "secret")
    with pytest.raises(ValueError, match="Invalid or corrupted"):
        decrypt_channex_api_key(other, enc)
