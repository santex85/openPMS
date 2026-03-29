"""Webhook HMAC signing (unit, no HTTP)."""

from __future__ import annotations

import hashlib
import hmac

from app.services.webhook_delivery_engine import sign_webhook_body


def test_sign_webhook_body_matches_hmac_sha256_hex() -> None:
    body = b'{"event":"booking.created","data":{"id":"x"}}'
    secret = "whsec_test"
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sign_webhook_body(secret, body) == expected
