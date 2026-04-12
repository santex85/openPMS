"""Unit tests for Stripe account id Fernet helpers."""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.stripe_secrets import decrypt_stripe_account_id, encrypt_stripe_account_id


def test_encrypt_decrypt_stripe_account_id_roundtrip() -> None:
    key = Fernet.generate_key().decode("ascii")
    settings = Settings.model_construct(
        database_url="postgresql://localhost/test",
        jwt_secret="a" * 32,
        webhook_secret_fernet_key=key,
    )
    plain = "acct_12345"
    enc = encrypt_stripe_account_id(settings, plain)
    assert enc != plain
    assert decrypt_stripe_account_id(settings, enc) == plain
