"""Unit tests for app/core/security.py and app/core/jwt_keys.py (no database)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.core.jwt_keys import decode_access_token, encode_token, jwt_signing_material
from app.core.security import (
    hash_password,
    hash_refresh_token,
    new_refresh_token_value,
    verify_password,
)


def test_hash_and_verify_correct_password() -> None:
    plain = "correct-horse-battery-stable"
    h = hash_password(plain)
    assert verify_password(plain, h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("right-password-8chars")
    assert verify_password("wrong-password-8chars", h) is False


def test_verify_invalid_hash_returns_false() -> None:
    assert verify_password("any", "not-a-valid-argon2-hash") is False


def test_hash_refresh_token_deterministic() -> None:
    raw = "opaque-refresh-raw-token"
    assert hash_refresh_token(raw) == hash_refresh_token(raw)


def test_new_refresh_token_value_length() -> None:
    v = new_refresh_token_value()
    assert len(v) >= 64


def _hs256_settings(secret: str | None = None) -> Settings:
    sec = secret if secret is not None else os.environ["JWT_SECRET"]
    return Settings(
        database_url="postgresql+asyncpg://localhost/example",
        jwt_secret=sec,
    )


def test_encode_decode_hs256_roundtrip() -> None:
    settings = _hs256_settings()
    tid = uuid4()
    sub = uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": str(sub),
        "tenant_id": str(tid),
        "role": "owner",
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = encode_token(settings, payload)
    out = decode_access_token(settings, token)
    assert out["tenant_id"] == str(tid)
    assert out["sub"] == str(sub)


def test_decode_expired_token_raises() -> None:
    settings = _hs256_settings()
    now = datetime.now(UTC)
    past_start = now - timedelta(hours=2)
    payload = {
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "role": "owner",
        "typ": "access",
        "iat": past_start,
        "exp": past_start + timedelta(minutes=5),
    }
    token = encode_token(settings, payload)
    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, token)


def test_decode_wrong_signature_raises() -> None:
    settings_a = Settings(
        database_url="postgresql+asyncpg://localhost/example",
        jwt_secret="a" * 32,
    )
    settings_b = Settings(
        database_url="postgresql+asyncpg://localhost/example",
        jwt_secret="b" * 32,
    )
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "role": "owner",
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = encode_token(settings_a, payload)
    with pytest.raises(InvalidTokenError):
        decode_access_token(settings_b, token)


def test_decode_missing_tenant_id_raises() -> None:
    settings = _hs256_settings()
    payload = {
        "sub": str(uuid4()),
        "role": "owner",
        "typ": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, token)


def test_jwt_signing_material_weak_secret_raises() -> None:
    settings = Settings.model_construct(
        database_url="postgresql+asyncpg://localhost/example",
        jwt_secret="short",
        jwt_algorithm="HS256",
    )
    with pytest.raises(RuntimeError, match="32"):
        jwt_signing_material(settings)


def test_jwt_signing_material_rs256_without_key_raises() -> None:
    settings = Settings.model_construct(
        database_url="postgresql+asyncpg://localhost/example",
        jwt_secret="x" * 32,
        jwt_algorithm="RS256",
        jwt_private_key_pem="",
    )
    with pytest.raises(RuntimeError, match="jwt_private_key_pem"):
        jwt_signing_material(settings)
