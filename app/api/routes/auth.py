"""Authentication: register, login, refresh, invite, current user."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    UserIdDep,
    require_roles,
)
from app.core.config import get_settings
from app.schemas.auth import (
    AuthInviteRequest,
    AuthInviteResponse,
    AuthLoginRequest,
    AuthLoginResponse,
    AuthRefreshRequest,
    AuthRegisterRequest,
    AuthRegisterResponse,
    TokenPairResponse,
    UserRead,
)
from app.services.auth_service import (
    AuthServiceError,
    get_user,
    invite_user,
    login as login_user,
    refresh_session,
    register_tenant_owner,
)

router = APIRouter()

WriteManagerDep = Annotated[None, Depends(require_roles("owner", "manager"))]


@router.post(
    "/register",
    response_model=AuthRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_register(request: Request, body: AuthRegisterRequest) -> AuthRegisterResponse:
    settings = get_settings()
    factory = request.app.state.async_session_factory
    tenant_id = uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            return await register_tenant_owner(
                session,
                settings,
                body,
                tenant_id=tenant_id,
            )


@router.post("/login", response_model=AuthLoginResponse)
async def post_login(request: Request, body: AuthLoginRequest) -> AuthLoginResponse:
    settings = get_settings()
    factory = request.app.state.async_session_factory
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(body.tenant_id)},
            )
            try:
                return await login_user(session, settings, body)
            except AuthServiceError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                ) from exc


@router.post("/refresh", response_model=TokenPairResponse)
async def post_refresh(request: Request, body: AuthRefreshRequest) -> TokenPairResponse:
    settings = get_settings()
    factory = request.app.state.async_session_factory
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(body.tenant_id)},
            )
            try:
                return await refresh_session(session, settings, body)
            except AuthServiceError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                ) from exc


@router.get("/me", response_model=UserRead)
async def get_me(
    session: SessionDep,
    tenant_id: TenantIdDep,
    user_id: UserIdDep,
) -> UserRead:
    user = await get_user(session, tenant_id, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )
    return UserRead.model_validate(user)


@router.post(
    "/invite",
    response_model=AuthInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_invite(
    _: WriteManagerDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: AuthInviteRequest,
) -> AuthInviteResponse:
    try:
        return await invite_user(session, tenant_id, body)
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
