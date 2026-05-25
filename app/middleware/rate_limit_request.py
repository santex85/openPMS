"""Bind the active Starlette request for SlowAPI exempt_when callbacks."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.rate_limit import bind_rate_limit_request, reset_rate_limit_request


class RateLimitRequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        token = bind_rate_limit_request(request)
        try:
            return await call_next(request)
        finally:
            reset_rate_limit_request(token)
