"""JWT signing and verification keys (HS256 vs RS256)."""

from __future__ import annotations

import jwt
from jwt.exceptions import InvalidTokenError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from app.core.config import Settings


def _normalize_pem(pem: str) -> str:
    s = pem.strip()
    if "\\n" in s and "\n" not in s:
        s = s.replace("\\n", "\n")
    return s


def _public_pem_from_private(private_pem: str) -> str:
    pem = _normalize_pem(private_pem)
    priv = serialization.load_pem_private_key(
        pem.encode("utf-8"),
        password=None,
        backend=default_backend(),
    )
    pub = priv.public_key()
    return (
        pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )


def jwt_signing_material(settings: Settings) -> tuple[str, str]:
    """Return (key_or_secret, algorithm) for jwt.encode."""
    alg = settings.jwt_algorithm.upper()
    if alg == "RS256":
        if not settings.jwt_private_key_pem or not settings.jwt_private_key_pem.strip():
            raise RuntimeError("jwt_private_key_pem is required when jwt_algorithm is RS256")
        return _normalize_pem(settings.jwt_private_key_pem), "RS256"
    if alg == "HS256":
        if len(settings.jwt_secret) < 32:
            raise RuntimeError("jwt_secret must be at least 32 characters when using HS256")
        return settings.jwt_secret, "HS256"
    raise RuntimeError(f"Unsupported jwt_algorithm: {settings.jwt_algorithm}")


def jwt_verifying_material(settings: Settings) -> tuple[str, str]:
    """Return (key_or_secret, algorithm) for jwt.decode."""
    alg = settings.jwt_algorithm.upper()
    if alg == "RS256":
        pub = settings.jwt_public_key_pem
        if pub and pub.strip():
            return _normalize_pem(pub), "RS256"
        if settings.jwt_private_key_pem and settings.jwt_private_key_pem.strip():
            return _public_pem_from_private(settings.jwt_private_key_pem), "RS256"
        raise RuntimeError(
            "jwt_public_key_pem or jwt_private_key_pem required for RS256 verification",
        )
    if alg == "HS256":
        if len(settings.jwt_secret) < 32:
            raise RuntimeError("jwt_secret must be at least 32 characters when using HS256")
        return settings.jwt_secret, "HS256"
    raise RuntimeError(f"Unsupported jwt_algorithm: {settings.jwt_algorithm}")


def decode_access_token(
    settings: Settings,
    token: str,
    *,
    audience: str | None = None,
    issuer: str | None = None,
) -> dict:
    key, algorithm = jwt_verifying_material(settings)
    kwargs: dict = {
        "algorithms": [algorithm],
        "options": {
            "require": ["exp", "sub"],
            "verify_signature": True,
            "verify_exp": True,
        },
    }
    if audience is not None:
        kwargs["audience"] = audience
    if issuer is not None:
        kwargs["issuer"] = issuer
    payload = jwt.decode(token, key, **kwargs)
    if payload.get("tenant_id") is None:
        raise InvalidTokenError("Missing required tenant_id claim")
    return payload


def encode_token(settings: Settings, payload: dict) -> str:
    key, algorithm = jwt_signing_material(settings)
    return jwt.encode(payload, key, algorithm=algorithm)
