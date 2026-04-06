"""Webhook subscription URLs: block non-public resolved IPs (SSRF hardening)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.webhook_url_validation import (
    WebhookUrlUnsafeError,
    assert_webhook_target_ips_safe_for_url,
)


def test_assert_webhook_target_rejects_loopback_literal() -> None:
    with pytest.raises(WebhookUrlUnsafeError, match="non-public"):
        assert_webhook_target_ips_safe_for_url("https://127.0.0.1/hooks/x")


def test_assert_webhook_target_rejects_ipv6_loopback_literal() -> None:
    with pytest.raises(WebhookUrlUnsafeError, match="non-public"):
        assert_webhook_target_ips_safe_for_url("https://[::1]/hooks/x")


def test_assert_webhook_target_rejects_private_literal() -> None:
    with pytest.raises(WebhookUrlUnsafeError, match="non-public"):
        assert_webhook_target_ips_safe_for_url("https://192.168.0.22/hooks/x")


def test_assert_webhook_target_rejects_non_https() -> None:
    with pytest.raises(WebhookUrlUnsafeError, match="HTTPS"):
        assert_webhook_target_ips_safe_for_url("http://example.com/hook")


def test_webhook_subscription_rejects_loopback_url(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/webhooks/subscriptions",
        headers=h,
        json={
            "url": "https://127.0.0.1/evil",
            "events": ["booking.created"],
            "is_active": True,
        },
    )
    assert r.status_code == 422
    body = r.json()["detail"].lower()
    assert "public" in body or "non-public" in body
