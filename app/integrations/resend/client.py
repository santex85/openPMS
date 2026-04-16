"""Resend email client (async; uses SDK HTTPX backend when available)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import resend
import structlog
from resend import Emails

from app.core.config import get_settings

log = structlog.get_logger()

_api_key_lock = asyncio.Lock()


class ResendNotConfiguredError(RuntimeError):
    """Raised when send_email is called but RESEND_API_KEY is not set."""


async def send_email(
    to: list[str],
    subject: str,
    html: str,
    *,
    from_: str | None = None,
    reply_to: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    """
    Send an HTML email via Resend. Returns the provider email id.

    Raises:
        ResendNotConfiguredError: If resend_api_key is empty.
    """
    settings = get_settings()
    key = (settings.resend_api_key or "").strip()
    if not key:
        msg = (
            "RESEND_API_KEY is not configured; cannot send email. "
            "Set RESEND_API_KEY in the environment (see .env.example)."
        )
        raise ResendNotConfiguredError(msg)

    params: dict[str, Any] = {
        "from": from_ or settings.email_from_default,
        "to": to,
        "subject": subject,
        "html": html,
    }
    if reply_to:
        params["reply_to"] = reply_to
    if attachments:
        params["attachments"] = cast(Any, attachments)

    async with _api_key_lock:
        previous = resend.api_key
        try:
            resend.api_key = key
            response = await Emails.send_async(cast(Any, params))
        finally:
            resend.api_key = previous

    email_id = str(response.id)
    log.info("resend_email_sent", email_id=email_id, to=to, subject=subject)
    return email_id
