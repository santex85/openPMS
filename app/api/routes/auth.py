"""Authentication: register, login, refresh, invite, current user."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.cookies_auth import attach_refresh_cookie, clear_refresh_cookie
from app.api.deps import (
    SessionDep,
    TenantIdDep,
    UserIdDep,
    require_jwt_user,
    require_roles,
)
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.rls_session import tenant_transaction_session
from app.schemas.auth import (
    AccessTokenResponse,
    AuthInviteRequest,
    AuthInviteResponse,
    AuthLoginPublicResponse,
    AuthLoginRequest,
    AuthRegisterPublicResponse,
    AuthRefreshRequest,
    AuthRegisterRequest,
    UserRead,
)
from app.services.audit_service import record_audit
from app.services.auth_service import (
    AuthServiceError,
    get_user,
    invite_user,
    list_users,
    login as login_user,
    refresh_session,
    register_tenant_owner,
)

router = APIRouter()

InviteManagerDep = Annotated[
    None,
    Depends(require_jwt_user()),
    Depends(require_roles("owner", "manager")),
]


@router.post(
    "/register",
    response_model=AuthRegisterPublicResponse,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
@limiter.limit("20/minute")
async def post_register(
    request: Request,
    response: Response,
    body: AuthRegisterRequest,
) -> AuthRegisterPublicResponse:
    settings = get_settings()
    factory = request.app.state.async_session_factory
    tenant_id = uuid4()
    try:
        async with tenant_transaction_session(factory, tenant_id) as session:
            full = await register_tenant_owner(
                session,
                settings,
                body,
                tenant_id=tenant_id,
            )
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    public = AuthRegisterPublicResponse(
        access_token=full.access_token,
        token_type=full.token_type,
        tenant_id=full.tenant_id,
        user=full.user,
    )
    attach_refresh_cookie(response, settings, full.refresh_token)
    return public


@router.post("/login", response_model=AuthLoginPublicResponse)
@limiter.limit("30/minute")
async def post_login(
    request: Request,
    response: Response,
    body: AuthLoginRequest,
) -> AuthLoginPublicResponse:
    settings = get_settings()
    factory = request.app.state.async_session_factory
    try:
        async with tenant_transaction_session(factory, body.tenant_id) as session:
            full = await login_user(session, settings, body)
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    public = AuthLoginPublicResponse(
        access_token=full.access_token,
        token_type=full.token_type,
        user=full.user,
    )
    attach_refresh_cookie(response, settings, full.refresh_token)
    return public


@router.post("/refresh", response_model=AccessTokenResponse)
@limiter.limit("60/minute")
async def post_refresh(
    request: Request,
    response: Response,
    body: AuthRefreshRequest,
) -> AccessTokenResponse:
    settings = get_settings()
    raw = body.refresh_token
    if raw is None or not str(raw).strip():
        raw = request.cookies.get(settings.refresh_cookie_name)
    if raw is None or not str(raw).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing refresh token",
        )
    body_filled = AuthRefreshRequest(
        tenant_id=body.tenant_id, refresh_token=str(raw).strip()
    )
    factory = request.app.state.async_session_factory
    try:
        async with tenant_transaction_session(factory, body.tenant_id) as session:
            full = await refresh_session(session, settings, body_filled)
    except AuthServiceError as exc:
        clear_refresh_cookie(response, settings)
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    attach_refresh_cookie(response, settings, full.refresh_token)
    return AccessTokenResponse(
        access_token=full.access_token, token_type=full.token_type
    )


@router.get("/users", response_model=list[UserRead])
async def get_users(
    _: InviteManagerDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[UserRead]:
    rows = await list_users(session, tenant_id)
    return [UserRead.model_validate(r) for r in rows]


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
    _: InviteManagerDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: AuthInviteRequest,
) -> AuthInviteResponse:
    try:
        out = await invite_user(session, tenant_id, body)
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="user.invite",
        entity_type="user",
        entity_id=out.user.id,
        new_values={"email": out.user.email, "role": out.user.role},
    )
    return out
