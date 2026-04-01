"""Authenticate via Bearer JWT (priority) or X-API-Key for integrations."""

from __future__ import annotations

from uuid import UUID

from jwt.exceptions import InvalidTokenError, PyJWTError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.audit_context import bind_audit_context, reset_audit_context
from app.core.config import get_settings
from app.core.jwt_keys import decode_access_token
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


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        return first or None
    if request.client:
        return request.client.host
    return None


async def _call_with_audit_asgi(
    request: Request,
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    uid = getattr(request.state, "user_id", None)
    user_id = uid if isinstance(uid, UUID) else None
    tok = bind_audit_context(user_id=user_id, ip_address=_client_ip(request))
    try:
        await app(scope, receive, send)
    finally:
        reset_audit_context(tok)


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

    try:
        payload = decode_access_token(
            settings,
            token,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
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
    result = await session.execute(
        text("SELECT tenant_id, key_id, scopes FROM lookup_api_key_by_hash(:h)"),
        {"h": digest},
    )
    row = result.first()
    if row is None:
        raise ValueError("unknown_key")

    tenant_id, key_id, scopes = row[0], row[1], row[2] or []
    request.state.tenant_id = tenant_id
    request.state.auth_source = "api_key"
    request.state.api_key_id = key_id
    request.state.api_key_scopes = [
        str(s).strip().lower() for s in scopes if str(s).strip()
    ]
    return True


async def _unauthorized_asgi(scope: Scope, receive: Receive, send: Send, message: str) -> None:
    body = UnauthorizedResponse(detail=message).model_dump()
    resp = JSONResponse(status_code=401, content=body)
    await resp(scope, receive, send)


class TenantJwtASGIMiddleware:
    """Bearer JWT takes precedence; otherwise X-API-Key for tenant + scopes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if _is_auth_exempt_path(request.url.path):
            await self.app(scope, receive, send)
            return

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            try:
                ok = await _authenticate_jwt(request)
            except ValueError:
                await _unauthorized_asgi(
                    scope, receive, send, "Invalid or expired token"
                )
                return
            if not ok:
                await _unauthorized_asgi(scope, receive, send, "Missing bearer token")
                return
            await _call_with_audit_asgi(request, self.app, scope, receive, send)
            return

        factory = request.app.state.async_session_factory
        async with factory() as session:
            try:
                ok = await _authenticate_api_key(request, session)
            except ValueError:
                await _unauthorized_asgi(scope, receive, send, "Invalid API key")
                return
            if ok:
                await _call_with_audit_asgi(request, self.app, scope, receive, send)
                return

        await _unauthorized_asgi(
            scope,
            receive,
            send,
            "Authenticate with Authorization: Bearer <JWT> or X-API-Key",
        )
