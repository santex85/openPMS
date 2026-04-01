"""Encrypt webhook signing secrets at rest (Fernet); legacy plaintext still supported for verify."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


def _fernet_instance(settings: Settings) -> Fernet:
    raw = (settings.webhook_secret_fernet_key or "").strip()
    if raw:
        key = raw.encode("ascii")
    else:
        digest = hashlib.sha256(
            b"openpms.webhook.fernet.v1\x00" + settings.jwt_secret.encode("utf-8"),
        ).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_webhook_secret(settings: Settings, plain: str) -> str:
    return _fernet_instance(settings).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_webhook_secret(settings: Settings, stored: str) -> str:
    """Return plaintext signing secret; treat non-Fernet rows as legacy plaintext."""
    try:
        return _fernet_instance(settings).decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError):
        return stored
