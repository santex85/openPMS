from collections.abc import AsyncIterator
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
            detail="tenant_id missing; use a JWT-protected route",
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


def require_roles(*allowed: str):
    allowed_set = frozenset(r.lower() for r in allowed)

    async def _runner(request: Request) -> None:
        role = getattr(request.state, "user_role", None)
        if role is None:
            role = "owner"
        else:
            role = role.lower()
        if role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )

    return _runner


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="tenant_id missing; use a JWT-protected route with get_db",
        )

    factory = request.app.state.async_session_factory
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]
