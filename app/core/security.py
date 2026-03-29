"""Password hashing (argon2id) and refresh-token hashing."""

from __future__ import annotations

import hashlib
import secrets
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher: Final[PasswordHasher] = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, plain)
        return True
    except VerifyMismatchError:
        return False


def new_refresh_token_value() -> str:
    """Opaque bearer value stored only client-side; DB keeps SHA-256 hex digest."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
