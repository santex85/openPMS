"""Register, login, refresh tokens, invite users."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.jwt_keys import encode_token
from app.core.security import (
    hash_password,
    hash_refresh_token,
    new_refresh_token_value,
    verify_password,
)
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.models.core.tenant import Tenant
from app.schemas.auth import (
    AuthInviteRequest,
    AuthLoginRequest,
    AuthLoginResponse,
    AuthRefreshRequest,
    AuthRegisterRequest,
    AuthRegisterResponse,
    AuthInviteResponse,
    TokenPairResponse,
    UserRead,
)


class AuthServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _issue_access_token(settings: Settings, user: User) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, object] = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "typ": "access",
        "iat": now,
        "exp": exp,
    }
    return encode_token(settings, payload)


async def _persist_refresh_pair(
    session: AsyncSession,
    settings: Settings,
    user: User,
) -> tuple[str, RefreshToken]:
    raw = new_refresh_token_value()
    digest = hash_refresh_token(raw)
    exp = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    row = RefreshToken(
        id=uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=digest,
        expires_at=exp,
        revoked_at=None,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return raw, row


async def register_tenant_owner(
    session: AsyncSession,
    settings: Settings,
    body: AuthRegisterRequest,
    *,
    tenant_id: UUID,
) -> AuthRegisterResponse:
    email_norm = body.email.strip().lower()
    tenant = Tenant(
        id=tenant_id,
        name=body.tenant_name.strip(),
        billing_email=email_norm,
        status="active",
    )
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        email=email_norm,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        role="owner",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    access = _issue_access_token(settings, user)
    refresh_raw, _ = await _persist_refresh_pair(session, settings, user)

    return AuthRegisterResponse(
        access_token=access,
        refresh_token=refresh_raw,
        tenant_id=tenant.id,
        user=UserRead.model_validate(user),
    )


async def login(
    session: AsyncSession,
    settings: Settings,
    body: AuthLoginRequest,
) -> AuthLoginResponse:
    email_norm = body.email.strip().lower()
    user = await session.scalar(
        select(User).where(
            User.tenant_id == body.tenant_id,
            User.email == email_norm,
        ),
    )
    if user is None or not user.is_active:
        raise AuthServiceError("invalid credentials", status_code=401)
    if not verify_password(body.password, user.password_hash):
        raise AuthServiceError("invalid credentials", status_code=401)

    access = _issue_access_token(settings, user)
    refresh_raw, _ = await _persist_refresh_pair(session, settings, user)

    return AuthLoginResponse(
        access_token=access,
        refresh_token=refresh_raw,
        user=UserRead.model_validate(user),
    )


async def refresh_session(
    session: AsyncSession,
    settings: Settings,
    body: AuthRefreshRequest,
) -> TokenPairResponse:
    raw = body.refresh_token
    if raw is None or not raw.strip():
        raise AuthServiceError("missing refresh token", status_code=401)
    digest = hash_refresh_token(raw.strip())
    now = datetime.now(UTC)
    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.tenant_id == body.tenant_id,
            RefreshToken.token_hash == digest,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        ),
    )
    if row is None:
        raise AuthServiceError("invalid refresh token", status_code=401)

    user = await session.scalar(
        select(User).where(
            User.tenant_id == row.tenant_id,
            User.id == row.user_id,
        ),
    )
    if user is None or not user.is_active:
        raise AuthServiceError("user inactive", status_code=401)

    row.revoked_at = now
    access = _issue_access_token(settings, user)
    refresh_raw, _ = await _persist_refresh_pair(session, settings, user)

    return TokenPairResponse(access_token=access, refresh_token=refresh_raw)


async def get_user(
    session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
) -> User | None:
    return await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.id == user_id,
        ),
    )


async def list_users(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[User]:
    result = await session.execute(
        select(User)
        .where(User.tenant_id == tenant_id)
        .order_by(User.email.asc()),
    )
    return list(result.scalars().all())


_INVITABLE_ROLES = frozenset({"manager", "viewer", "housekeeper", "receptionist"})


async def invite_user(
    session: AsyncSession,
    tenant_id: UUID,
    body: AuthInviteRequest,
) -> AuthInviteResponse:
    role = body.role.strip().lower()
    if role not in _INVITABLE_ROLES:
        raise AuthServiceError(
            "role must be one of: manager, viewer, housekeeper, receptionist",
            status_code=422,
        )
    email_norm = body.email.strip().lower()
    exists = await session.scalar(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.email == email_norm,
        ),
    )
    if exists is not None:
        raise AuthServiceError("user with this email already exists", status_code=409)

    temp_password = token_urlsafe(12)
    user = User(
        tenant_id=tenant_id,
        email=email_norm,
        password_hash=hash_password(temp_password),
        full_name=body.full_name.strip(),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    return AuthInviteResponse(
        user=UserRead.model_validate(user),
        temporary_password=temp_password,
    )
