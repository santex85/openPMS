"""Create and list API keys (hashed at rest)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.api_key import ApiKey


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key_plaintext() -> str:
    """Opaque secret shown once to the caller."""
    return f"opms_{secrets.token_urlsafe(32)}"


class ApiKeyServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def create_api_key(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
) -> tuple[ApiKey, str]:
    trimmed = name.strip()
    if not trimmed:
        raise ApiKeyServiceError("name is required", status_code=422)
    norm_scopes = [s.strip().lower() for s in scopes if s.strip()]
    if not norm_scopes:
        raise ApiKeyServiceError("at least one scope is required", status_code=422)
    plain = generate_api_key_plaintext()
    row = ApiKey(
        id=uuid4(),
        tenant_id=tenant_id,
        key_hash=hash_api_key(plain),
        name=trimmed,
        scopes=norm_scopes,
        is_active=True,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row, plain


async def list_api_keys(session: AsyncSession, tenant_id: UUID) -> list[ApiKey]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.tenant_id == tenant_id)
        .order_by(ApiKey.name.asc(), ApiKey.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def patch_api_key(
    session: AsyncSession,
    tenant_id: UUID,
    key_id: UUID,
    *,
    is_active: bool | None,
    name: str | None,
) -> ApiKey:
    row = await session.scalar(
        select(ApiKey).where(
            ApiKey.tenant_id == tenant_id,
            ApiKey.id == key_id,
        ),
    )
    if row is None:
        raise ApiKeyServiceError("API key not found", status_code=404)
    if name is not None:
        t = name.strip()
        if not t:
            raise ApiKeyServiceError("name cannot be empty", status_code=422)
        row.name = t
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return row
