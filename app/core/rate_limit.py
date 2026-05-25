"""Shared SlowAPI limiter (expects app.state.limiter in create_app)."""

from __future__ import annotations

import secrets
from contextvars import ContextVar, Token
from uuid import UUID

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

MIGRATION_RATE_LIMIT_HEADER = "X-OpenPMS-Migration-Key"

_current_request: ContextVar[Request | None] = ContextVar(
    "_rate_limit_request",
    default=None,
)


def bind_rate_limit_request(request: Request) -> Token[Request | None]:
    """Store the active request for SlowAPI exempt_when callbacks (no-arg API)."""
    return _current_request.set(request)


def reset_rate_limit_request(token: Token[Request | None]) -> None:
    _current_request.reset(token)


def rate_limit_key(request: Request) -> str:
    """
    Bucket authenticated traffic per tenant and client IP.
    Unauthenticated paths (e.g. /auth/register) use anon:<ip>.
    Requires TenantJwtASGIMiddleware to run before SlowAPIMiddleware.
    """
    ip = get_remote_address(request) or "unknown"
    tid = getattr(request.state, "tenant_id", None)
    if isinstance(tid, UUID):
        return f"{tid}:{ip}"
    return f"anon:{ip}"


def migration_rate_limit_exempt() -> bool:
    """
    Skip per-route limits for the migration CLI when it sends a matching secret header.
    Requires MIGRATION_RATE_LIMIT_KEY in API settings; empty disables bypass.
    Called by SlowAPI with no arguments — reads request from bind_rate_limit_request().
    """
    request = _current_request.get()
    if request is None:
        return False
    from app.core.config import get_settings

    expected = (get_settings().migration_rate_limit_key or "").strip()
    if not expected:
        return False
    supplied = (request.headers.get(MIGRATION_RATE_LIMIT_HEADER) or "").strip()
    if not supplied:
        return False
    return secrets.compare_digest(supplied, expected)


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=["300/minute"],
)
