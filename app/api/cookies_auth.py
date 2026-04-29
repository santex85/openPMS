"""HttpOnly refresh cookie for browser clients."""

from __future__ import annotations

from starlette.responses import Response

from app.core.config import Settings


def attach_refresh_cookie(
    response: Response, settings: Settings, refresh_raw: str
) -> None:
    max_age = settings.refresh_token_ttl_days * 24 * 3600
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_raw,
        httponly=True,
        max_age=max_age,
        samesite="lax",
        secure=settings.refresh_cookie_secure,
        path="/",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/",
        secure=settings.refresh_cookie_secure,
        samesite="lax",
        httponly=True,
    )
