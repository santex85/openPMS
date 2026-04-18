"""Stripe Connect Standard OAuth: authorize URL, token exchange, disconnect."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.stripe_secrets import decrypt_stripe_account_id, encrypt_stripe_account_id
from app.models.billing.stripe_connection import StripeConnection
from app.models.core.property import Property
from app.schemas.stripe_connect import StripeStatusRead

_STATE_MAX_AGE_SEC = 3600


class StripeConnectError(Exception):
    """Business / validation error for Connect flow."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def oauth_state_hmac_key(settings: Settings) -> str:
    if (settings.stripe_oauth_state_secret or "").strip():
        return settings.stripe_oauth_state_secret.strip()
    if settings.jwt_algorithm.upper() == "HS256":
        return settings.jwt_secret
    w = (settings.webhook_secret_fernet_key or "").strip()
    if w:
        return w
    msg = (
        "Set STRIPE_OAUTH_STATE_SECRET (or WEBHOOK_SECRET_FERNET_KEY in RS256 mode) "
        "for Stripe OAuth state signing"
    )
    raise ValueError(msg)


def encode_oauth_state(settings: Settings, tenant_id: UUID, property_id: UUID) -> str:
    key = oauth_state_hmac_key(settings)
    payload = {
        "p": str(property_id),
        "t": str(tenant_id),
        "ts": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = hmac.new(key.encode("utf-8"), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_oauth_state(settings: Settings, state: str) -> tuple[UUID, UUID]:
    key = oauth_state_hmac_key(settings)
    try:
        b64, sig = state.split(".", 1)
    except ValueError as exc:
        raise StripeConnectError("invalid state", status_code=400) from exc
    expected = hmac.new(key.encode("utf-8"), b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise StripeConnectError("invalid state", status_code=400)
    pad = "=" * ((4 - len(b64) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(b64 + pad)
        payload = json.loads(raw.decode("utf-8"))
        tenant_id = UUID(str(payload["t"]))
        property_id = UUID(str(payload["p"]))
        ts = int(payload["ts"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise StripeConnectError("invalid state", status_code=400) from exc
    if int(time.time()) - ts > _STATE_MAX_AGE_SEC:
        raise StripeConnectError("state expired", status_code=400)
    return tenant_id, property_id


def _require_stripe_oauth_settings(settings: Settings) -> None:
    if not (settings.stripe_client_id or "").strip():
        raise StripeConnectError("Stripe client_id is not configured", status_code=503)
    if not (settings.stripe_redirect_uri or "").strip():
        raise StripeConnectError(
            "Stripe redirect_uri is not configured", status_code=503
        )


def _require_stripe_secret(settings: Settings) -> None:
    if not (settings.stripe_secret_key or "").strip():
        raise StripeConnectError("Stripe secret key is not configured", status_code=503)


def build_connect_authorize_url(
    settings: Settings,
    tenant_id: UUID,
    property_id: UUID,
) -> str:
    _require_stripe_oauth_settings(settings)
    state = encode_oauth_state(settings, tenant_id, property_id)
    return stripe.OAuth.authorize_url(
        client_id=settings.stripe_client_id.strip(),
        response_type="code",
        scope="read_write",
        redirect_uri=settings.stripe_redirect_uri.strip(),
        state=state,
    )


async def exchange_code_for_connection(
    settings: Settings,
    session: AsyncSession,
    code: str,
    state: str,
) -> StripeConnection:
    _require_stripe_secret(settings)
    _require_stripe_oauth_settings(settings)
    tenant_id, property_id = decode_oauth_state(settings, state)
    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise StripeConnectError("property not found", status_code=404)
    try:
        token_resp = stripe.OAuth.token(
            api_key=settings.stripe_secret_key.strip(),
            grant_type="authorization_code",
            code=code,
        )
    except stripe.StripeError as exc:
        raise StripeConnectError(
            getattr(exc, "user_message", None) or str(exc),
            status_code=400,
        ) from exc
    stripe_user_id = getattr(token_resp, "stripe_user_id", None)
    livemode_raw = getattr(token_resp, "livemode", None)
    if not stripe_user_id:
        raise StripeConnectError(
            "Stripe did not return stripe_user_id", status_code=502
        )
    livemode = bool(livemode_raw)
    ciphertext = encrypt_stripe_account_id(settings, str(stripe_user_id))
    now = datetime.now(UTC)
    existing = await session.scalar(
        select(StripeConnection).where(
            StripeConnection.tenant_id == tenant_id,
            StripeConnection.property_id == property_id,
        ),
    )
    if existing:
        existing.stripe_account_id = ciphertext
        existing.livemode = livemode
        existing.connected_at = now
        existing.disconnected_at = None
        await session.flush()
        await session.refresh(existing)
        return existing
    row = StripeConnection(
        tenant_id=tenant_id,
        property_id=property_id,
        stripe_account_id=ciphertext,
        livemode=livemode,
        connected_at=now,
        disconnected_at=None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_stripe_status(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> StripeStatusRead:
    row = await session.scalar(
        select(StripeConnection).where(
            StripeConnection.tenant_id == tenant_id,
            StripeConnection.property_id == property_id,
            StripeConnection.disconnected_at.is_(None),
        ),
    )
    if row is None:
        return StripeStatusRead(status="not_connected")
    return StripeStatusRead(
        status="connected",
        livemode=row.livemode,
        connected_at=row.connected_at,
    )


async def disconnect_stripe_connection(
    settings: Settings,
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> None:
    _require_stripe_secret(settings)
    _require_stripe_oauth_settings(settings)
    row = await session.scalar(
        select(StripeConnection).where(
            StripeConnection.tenant_id == tenant_id,
            StripeConnection.property_id == property_id,
            StripeConnection.disconnected_at.is_(None),
        ),
    )
    if row is None:
        raise StripeConnectError(
            "Stripe is not connected for this property", status_code=404
        )
    plain = decrypt_stripe_account_id(settings, row.stripe_account_id)
    try:
        stripe.OAuth.deauthorize(
            api_key=settings.stripe_secret_key.strip(),
            stripe_user_id=plain,
            client_id=settings.stripe_client_id.strip(),
        )
    except stripe.StripeError as exc:
        raise StripeConnectError(
            getattr(exc, "user_message", None) or str(exc),
            status_code=502,
        ) from exc
    row.disconnected_at = datetime.now(UTC)
    await session.flush()
