"""Shared SlowAPI limiter (expects app.state.limiter in create_app)."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


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


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=["300/minute"],
)
