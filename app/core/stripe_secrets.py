"""Encrypt Stripe Connect account IDs at rest (reuses webhook Fernet key material)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.webhook_secrets import get_fernet


def encrypt_stripe_account_id(settings: Settings, plain: str) -> str:
    return get_fernet(settings).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_stripe_account_id(settings: Settings, stored: str) -> str:
    return get_fernet(settings).decrypt(stored.encode("ascii")).decode("utf-8")
