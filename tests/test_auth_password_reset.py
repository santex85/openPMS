"""POST /auth/forgot-password and /auth/reset-password — reset flow + guardrails."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import jwt

from app.core.config import get_settings
from app.services.auth_service import request_password_reset


def _extract_token(reset_link: str) -> str:
    parsed = urlparse(reset_link)
    tokens = parse_qs(parsed.query).get("token")
    assert tokens, f"no token in reset link: {reset_link}"
    return tokens[0]


def _capture_reset_link() -> tuple[AsyncMock, dict[str, str]]:
    captured: dict[str, str] = {}

    async def _side_effect(_session, _tenant_id, **kwargs) -> None:
        captured["reset_link"] = kwargs["reset_link"]
        captured["to_email"] = kwargs["to_email"]

    return AsyncMock(side_effect=_side_effect), captured


def test_forgot_password_unknown_email_returns_204_without_email(client) -> None:
    with patch(
        "app.services.auth_service.send_password_reset_email",
        new_callable=AsyncMock,
    ) as mock_send:
        r = client.post(
            "/auth/forgot-password",
            json={"email": f"nobody-{uuid4()}@example.com"},
        )
    assert r.status_code == 204, r.text
    mock_send.assert_not_awaited()


def test_forgot_password_known_email_sends_reset_link(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    mock_send, captured = _capture_reset_link()
    with patch("app.services.auth_service.send_password_reset_email", mock_send):
        r = client.post(
            "/auth/forgot-password",
            json={"email": "owner@smoke.example.com"},
        )
    assert r.status_code == 204, r.text
    # At least one active account matches (other tests may seed the same email).
    mock_send.assert_awaited()
    assert captured["to_email"] == "owner@smoke.example.com"
    assert "/reset-password?token=" in captured["reset_link"]


def test_reset_password_happy_path_then_login(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    tid = smoke_scenario["tenant_id"]
    mock_send, captured = _capture_reset_link()
    with patch("app.services.auth_service.send_password_reset_email", mock_send):
        client.post("/auth/forgot-password", json={"email": "owner@smoke.example.com"})
    token = _extract_token(captured["reset_link"])

    new_password = "brand-new-pass-123"
    rr = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": new_password},
    )
    assert rr.status_code == 204, rr.text

    lr = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": "owner@smoke.example.com",
            "password": new_password,
        },
    )
    assert lr.status_code == 200, lr.text


def test_reset_password_invalid_token_returns_401(client) -> None:
    r = client.post(
        "/auth/reset-password",
        json={"token": "not-a-real-jwt", "new_password": "whatever-123"},
    )
    assert r.status_code == 401, r.text


def test_reset_password_malformed_tenant_claim_returns_401(client) -> None:
    """Valid JWT shape but tenant_id is not a UUID -> 401 (not a 500)."""
    secret = os.environ["JWT_SECRET"]
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "tenant_id": "not-a-uuid",
            "typ": "password_reset",
            "pwd_fp": "abcdef012345",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "whatever-123"},
    )
    assert r.status_code == 401, r.text


def test_forgot_password_send_failure_still_returns_204(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    with patch(
        "app.services.auth_service.send_password_reset_email",
        new_callable=AsyncMock,
        side_effect=RuntimeError("resend down"),
    ):
        r = client.post(
            "/auth/forgot-password",
            json={"email": "owner@smoke.example.com"},
        )
    assert r.status_code == 204, r.text


def test_reset_password_token_single_use_after_change(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    mock_send, captured = _capture_reset_link()
    with patch("app.services.auth_service.send_password_reset_email", mock_send):
        client.post("/auth/forgot-password", json={"email": "owner@smoke.example.com"})
    token = _extract_token(captured["reset_link"])

    first = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "first-new-pass-1"},
    )
    assert first.status_code == 204, first.text

    # Same token embeds the old password fingerprint -> now stale.
    second = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "second-new-pass-2"},
    )
    assert second.status_code == 401, second.text


def test_reset_password_short_password_returns_422(
    client, smoke_scenario: dict[str, UUID]
) -> None:
    mock_send, captured = _capture_reset_link()
    with patch("app.services.auth_service.send_password_reset_email", mock_send):
        client.post("/auth/forgot-password", json={"email": "owner@smoke.example.com"})
    token = _extract_token(captured["reset_link"])
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "short"},
    )
    assert r.status_code == 422, r.text


def test_reset_password_unknown_user_returns_401(client) -> None:
    """Well-formed token whose user/tenant no longer exists -> 401."""
    secret = os.environ["JWT_SECRET"]
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "tenant_id": str(uuid4()),
            "typ": "password_reset",
            "pwd_fp": "abcdef012345",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "whatever-123"},
    )
    assert r.status_code == 401, r.text


async def test_request_password_reset_swallows_lookup_failure() -> None:
    """A DB error during the email lookup must not raise (anti-enumeration)."""

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db unavailable")

    # Should complete without raising.
    await request_password_reset(_BoomFactory(), get_settings(), "boom@example.com")


def test_forgot_password_rate_limited_after_5(client) -> None:
    with patch(
        "app.services.auth_service.send_password_reset_email",
        new_callable=AsyncMock,
    ):
        codes = [
            client.post(
                "/auth/forgot-password",
                json={"email": f"rl-{uuid4()}@example.com"},
            ).status_code
            for _ in range(6)
        ]
    assert codes[:5] == [204, 204, 204, 204, 204]
    assert codes[5] == 429
