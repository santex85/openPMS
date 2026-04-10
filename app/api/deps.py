from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def get_tenant_id(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="tenant_id missing; authenticate with Bearer JWT or X-API-Key",
        )
    return tenant_id


TenantIdDep = Annotated[UUID, Depends(get_tenant_id)]


def get_user_id(request: Request) -> UUID:
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token must include user subject (sub) for this route",
        )
    return user_id


UserIdDep = Annotated[UUID, Depends(get_user_id)]


def get_optional_user_id_for_audit(request: Request) -> UUID | None:
    """JWT: required sub for user-attributed writes; API key: no user (audit fields nullable)."""
    if getattr(request.state, "auth_source", "jwt") == "api_key":
        return None
    uid = getattr(request.state, "user_id", None)
    if isinstance(uid, UUID):
        return uid
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Access token must include user subject (sub) for this route",
    )


OptionalUserIdWriteDep = Annotated[
    UUID | None,
    Depends(get_optional_user_id_for_audit),
]


def chain_dependency_runners(
    *runners: Callable[[Request], Awaitable[None]],
) -> Callable[[Request], Awaitable[None]]:
    """
    FastAPI only honors the last Depends() inside Annotated[None, Depends(...), ...].
    Compose multiple security runners (JWT / roles / scopes) into a single dependency.
    """
    seq = tuple(runners)

    async def _combined(request: Request) -> None:
        for runner in seq:
            await runner(request)

    return _combined


def require_jwt_user() -> Callable:
    """Reject API keys (e.g. managing API keys must use interactive JWT)."""

    async def _runner(request: Request) -> None:
        if getattr(request.state, "auth_source", "jwt") != "jwt":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This operation requires a user JWT, not an API key",
            )

    return _runner


def require_roles(*allowed: str) -> Callable:
    allowed_set = frozenset(r.lower() for r in allowed)

    async def _runner(request: Request) -> None:
        if getattr(request.state, "auth_source", "jwt") != "jwt":
            return None
        role = getattr(request.state, "user_role", None)
        if not isinstance(role, str) or not role.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        role = role.strip().lower()
        if role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )

    return _runner


def require_scopes(*required: str) -> Callable:
    """
    For X-API-Key auth: require each listed scope (or '*' wildcard on the key).
    JWT auth skips this check (RBAC uses require_roles).
    """
    needed = tuple(s.lower() for s in required)

    async def _runner(request: Request) -> None:
        if getattr(request.state, "auth_source", "jwt") != "api_key":
            return None
        if not needed:
            return None
        raw = getattr(request.state, "api_key_scopes", None) or ()
        granted = frozenset(str(s).strip().lower() for s in raw if str(s).strip())
        if "*" in granted:
            return None
        if all(n in granted for n in needed):
            return None
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key lacks required scope",
        )

    return _runner


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="tenant_id missing; authenticate with Bearer JWT or X-API-Key",
        )

    factory = request.app.state.async_session_factory
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_db)]
