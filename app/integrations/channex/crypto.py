"""Encrypt Channex API keys at rest (same Fernet key as webhook subscription secrets)."""

from __future__ import annotations

from cryptography.fernet import InvalidToken

from app.core.config import Settings
from app.core.webhook_secrets import get_fernet


def encrypt_channex_api_key(settings: Settings, plain: str) -> str:
    return get_fernet(settings).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_channex_api_key(settings: Settings, stored: str) -> str:
    """Decrypt a stored Channex API key; invalid ciphertext is an error (no legacy plaintext)."""
    try:
        return get_fernet(settings).decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        msg = "Invalid or corrupted Channex API key ciphertext"
        raise ValueError(msg) from exc
