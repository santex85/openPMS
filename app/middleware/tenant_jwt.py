"""Authenticate via Bearer JWT (priority) or X-API-Key for integrations."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError, PyJWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.models.auth.api_key import ApiKey
from app.schemas.auth import UnauthorizedResponse
from app.services.api_key_service import hash_api_key


def _is_auth_exempt_path(path: str) -> bool:
    exempt = {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/favicon.ico",
        "/auth/register",
        "/auth/login",
        "/auth/refresh",
    }
    if path in exempt:
        return True
    if path.startswith("/docs/") or path.startswith("/redoc/"):
        return True
    return False


async def _authenticate_jwt(request: Request) -> bool:
    """
    Validate Bearer JWT and populate request.state.
    Returns True on success, False if no Bearer header.
    Raises no return on failure — caller must return 401 Response.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return False

    settings = get_settings()
    decode_kwargs: dict[str, object] = {
        "algorithms": [settings.jwt_algorithm],
    }
    if settings.jwt_audience is not None:
        decode_kwargs["audience"] = settings.jwt_audience
    if settings.jwt_issuer is not None:
        decode_kwargs["issuer"] = settings.jwt_issuer

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            **decode_kwargs,
        )
    except InvalidTokenError:
        raise ValueError("invalid_token")
    except PyJWTError:
        raise ValueError("invalid_token")

    raw_tenant = payload.get("tenant_id")
    if raw_tenant is None:
        raise ValueError("missing_tenant")

    try:
        tenant_id = UUID(str(raw_tenant))
    except ValueError:
        raise ValueError("bad_tenant")

    request.state.tenant_id = tenant_id
    request.state.auth_source = "jwt"

    raw_sub = payload.get("sub")
    if raw_sub is not None:
        try:
            request.state.user_id = UUID(str(raw_sub))
        except ValueError:
            raise ValueError("bad_sub")

    raw_role = payload.get("role")
    if isinstance(raw_role, str) and raw_role:
        trimmed = raw_role.strip()
        if trimmed:
            request.state.user_role = trimmed.lower()

    return True


async def _authenticate_api_key(request: Request, session: AsyncSession) -> bool:
    raw = request.headers.get("X-API-Key")
    if not raw or not raw.strip():
        return False

    digest = hash_api_key(raw.strip())
    stmt = (
        select(ApiKey.tenant_id, ApiKey.id, ApiKey.scopes)
        .where(
            ApiKey.key_hash == digest,
            ApiKey.is_active.is_(True),
        )
        .where(
            (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > func.now()),
        )
    )

    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        raise ValueError("unknown_key")

    tenant_id, key_id, scopes = row[0], row[1], row[2] or []
    request.state.tenant_id = tenant_id
    request.state.auth_source = "api_key"
    request.state.api_key_id = key_id
    request.state.api_key_scopes = [str(s).strip().lower() for s in scopes if str(s).strip()]
    return True


class TenantJwtMiddleware(BaseHTTPMiddleware):
    """Bearer JWT takes precedence; otherwise X-API-Key for tenant + scopes."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        if _is_auth_exempt_path(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            try:
                ok = await _authenticate_jwt(request)
            except ValueError:
                return _unauthorized_response("Invalid or expired token")
            if not ok:
                return _unauthorized_response("Missing bearer token")
            return await call_next(request)

        factory = request.app.state.async_session_factory
        async with factory() as session:
            try:
                ok = await _authenticate_api_key(request, session)
            except ValueError:
                return _unauthorized_response("Invalid API key")
            if ok:
                return await call_next(request)

        return _unauthorized_response(
            "Authenticate with Authorization: Bearer <JWT> or X-API-Key",
        )


def _unauthorized_response(message: str) -> JSONResponse:
    body = UnauthorizedResponse(detail=message).model_dump()
    return JSONResponse(status_code=401, content=body)
