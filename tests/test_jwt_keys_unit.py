"""Unit tests for app.core.jwt_keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.core.jwt_keys import (
    decode_access_token,
    jwt_signing_material,
    jwt_verifying_material,
)


def _rs256_settings() -> Settings:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_priv = (
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("utf-8")
    )
    return Settings.model_construct(
        database_url="postgresql://localhost:5432/openpms_test",
        jwt_algorithm="RS256",
        jwt_private_key_pem=pem_priv,
        jwt_public_key_pem=None,
        jwt_secret="",
        webhook_secret_fernet_key=Fernet.generate_key().decode(),
    )


def test_jwt_signing_rs256_and_verifying_public_from_private() -> None:
    settings = _rs256_settings()
    key, alg = jwt_signing_material(settings)
    assert alg == "RS256"
    assert "BEGIN PRIVATE KEY" in key

    vkey, valg = jwt_verifying_material(settings)
    assert valg == "RS256"
    assert "BEGIN PUBLIC KEY" in vkey


def test_jwt_unsupported_algorithm() -> None:
    settings = Settings.model_construct(
        database_url="postgresql://localhost:5432/openpms_test",
        jwt_algorithm="ES256",
        jwt_secret="x" * 32,
        webhook_secret_fernet_key=Fernet.generate_key().decode(),
    )
    with pytest.raises(RuntimeError, match="Unsupported jwt_algorithm"):
        jwt_signing_material(settings)


def test_decode_access_token_missing_tenant_id() -> None:
    settings = Settings.model_construct(
        database_url="postgresql://localhost:5432/openpms_test",
        jwt_algorithm="HS256",
        jwt_secret="a" * 32,
    )
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError, match="tenant_id"):
        decode_access_token(settings, token)
