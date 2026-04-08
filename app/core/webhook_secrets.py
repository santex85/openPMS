"""Encrypt webhook signing secrets at rest (Fernet); legacy plaintext still supported for verify."""

from __future__ import annotations

import base64
import hashlib

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

log = structlog.get_logger()
_derived_fernet_warning_emitted: bool = False


def get_fernet(settings: Settings) -> Fernet:
    global _derived_fernet_warning_emitted
    raw = (settings.webhook_secret_fernet_key or "").strip()
    if raw:
        key = raw.encode("ascii")
    else:
        if not _derived_fernet_warning_emitted:
            log.warning(
                "webhook_fernet_key_derived_from_jwt",
                jwt_algorithm=settings.jwt_algorithm,
            )
            _derived_fernet_warning_emitted = True
        digest = hashlib.sha256(
            b"openpms.webhook.fernet.v1\x00" + settings.jwt_secret.encode("utf-8"),
        ).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_webhook_secret(settings: Settings, plain: str) -> str:
    return get_fernet(settings).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_webhook_secret(settings: Settings, stored: str) -> str:
    """Return plaintext signing secret; treat non-Fernet rows as legacy plaintext."""
    try:
        return get_fernet(settings).decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError):
        return stored


def encrypt_plaintext_with_fernet_key(plain: str, key_ascii: str) -> str:
    """
    Encrypt plaintext using an explicit Fernet key (ASCII URL-safe base64 from
    Fernet.generate_key()). Used when rotating WEBHOOK_SECRET_FERNET_KEY via API.
    """
    trimmed = (key_ascii or "").strip()
    if not trimmed:
        raise ValueError("Fernet key is required")
    try:
        f = Fernet(trimmed.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid Fernet key material") from exc
    return f.encrypt(plain.encode("utf-8")).decode("ascii")
