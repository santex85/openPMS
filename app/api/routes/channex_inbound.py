"""Inbound Channex webhooks (public POST; no JWT)."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import text

from app.core.config import get_settings
from app.models.integrations.channex_webhook_log import ChannexWebhookLog

router = APIRouter()
log = structlog.get_logger()

_CHANNEX_IP_NET = ipaddress.ip_network("34.76.12.0/24")


@router.post("/channex")
async def inbound_channex_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Accept Channex webhook; persist payload and queue async processing."""
    body = await request.body()
    settings = get_settings()

    secret = (settings.channex_webhook_secret or "").strip()
    if secret:
        sig_header = request.headers.get("X-Channex-Signature") or request.headers.get(
            "x-channex-signature",
        )
        if not sig_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing signature",
            )
        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected.lower(), sig_header.strip().lower()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )
    elif settings.channex_webhook_verify_channex_ips:
        host = request.client.host if request.client else None
        if host is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing client IP",
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid client IP",
            ) from None
        if ip not in _CHANNEX_IP_NET:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden source IP",
            )

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {
            "_parse_error": True,
            "raw_preview": body[:500].decode("utf-8", errors="replace"),
        }

    cx_prop = ""
    if isinstance(payload, dict):
        cx_prop = str(payload.get("property_id") or "").strip()

    factory = request.app.state.async_session_factory
    webhook_id_str: str
    async with factory() as session:
        async with session.begin():
            tenant_uuid = None
            if cx_prop:
                res = await session.execute(
                    text(
                        "SELECT tenant_id FROM lookup_channex_link_for_webhook(:p) LIMIT 1",
                    ),
                    {"p": cx_prop},
                )
                r0 = res.first()
                if r0 is not None:
                    tenant_uuid = r0[0]
            ev = payload.get("event") if isinstance(payload, dict) else None
            log_row = ChannexWebhookLog(
                tenant_id=tenant_uuid,
                event_type=str(ev) if ev is not None else None,
                payload=payload if isinstance(payload, dict) else {"invalid": True},
                signature=request.headers.get("X-Channex-Signature")
                or request.headers.get("x-channex-signature"),
                ip_address=request.client.host if request.client else None,
                processed=False,
            )
            session.add(log_row)
            await session.flush()
            webhook_id_str = str(log_row.id)

    def _enqueue() -> None:
        from app.tasks.channex_webhook_task import channex_process_webhook

        channex_process_webhook.delay(webhook_id_str)

    background_tasks.add_task(_enqueue)
    log.info("channex_webhook_accepted", webhook_log_id=webhook_id_str)
    return {"status": "ok"}
