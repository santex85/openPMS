"""Rate limiting returns 429 when the per-route limit is exceeded."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture
def rate_limit_client():
    limiter.reset()
    with TestClient(app, base_url="http://test") as c:
        yield c
    limiter.reset()


def test_register_events_hit_rate_limit_429(rate_limit_client: TestClient) -> None:
    """POST /auth/register is limited to 10/minute; the 11th call in-window is 429."""
    codes: list[int] = []
    for i in range(11):
        r = rate_limit_client.post(
            "/auth/register",
            json={
                "tenant_name": f"RL{i}",
                "email": f"rl{i}@rate-limit.example.com",
                "password": "longenoughpassword",
                "full_name": "RL",
            },
        )
        codes.append(r.status_code)
    assert 429 in codes
    assert any(c == 201 for c in codes)


def test_login_events_hit_rate_limit_429(rate_limit_client: TestClient) -> None:
    """POST /auth/login is limited to 10/minute; brute-force gets 429."""
    from uuid import uuid4

    tid = uuid4()
    codes: list[int] = []
    for i in range(11):
        r = rate_limit_client.post(
            "/auth/login",
            json={
                "tenant_id": str(tid),
                "email": f"nobody{i}@example.com",
                "password": "wrong-password-xyz",
            },
        )
        codes.append(r.status_code)
    assert 429 in codes
    assert all(c in (401, 429) for c in codes)
