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
    """POST /auth/register is limited to 20/minute; the 21st call in-window is 429."""
    codes: list[int] = []
    for i in range(21):
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
