"""Extract tenant_id from HS256 JWT and attach to request.state."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError, PyJWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.schemas.auth import UnauthorizedResponse


def _is_auth_exempt_path(path: str) -> bool:
    exempt = {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/favicon.ico",
    }
    if path in exempt:
        return True
    if path.startswith("/docs/") or path.startswith("/redoc/"):
        return True
    return False


class TenantJwtMiddleware(BaseHTTPMiddleware):
    """Require a valid Bearer JWT with a tenant_id claim on non-exempt routes."""

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
        if not auth_header or not auth_header.startswith("Bearer "):
            return _unauthorized_response("Missing or invalid Authorization header")

        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            return _unauthorized_response("Missing bearer token")

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
            return _unauthorized_response("Invalid or expired token")
        except PyJWTError:
            return _unauthorized_response("Token verification failed")

        raw_tenant = payload.get("tenant_id")
        if raw_tenant is None:
            return _unauthorized_response("Token missing tenant_id claim")

        try:
            tenant_id = UUID(str(raw_tenant))
        except ValueError:
            return _unauthorized_response("tenant_id must be a valid UUID")

        request.state.tenant_id = tenant_id
        return await call_next(request)


def _unauthorized_response(message: str) -> JSONResponse:
    body = UnauthorizedResponse(detail=message).model_dump()
    return JSONResponse(status_code=401, content=body)
