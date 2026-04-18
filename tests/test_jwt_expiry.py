"""Expired JWT is rejected by auth middleware."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt


def test_expired_jwt_returns_401(
    client,
    smoke_scenario: dict[str, UUID],
    jwt_secret: str,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    token = jwt.encode(
        {
            "tenant_id": str(tid),
            "sub": str(oid),
            "role": "owner",
            "exp": datetime.now(UTC) - timedelta(minutes=5),
        },
        jwt_secret,
        algorithm="HS256",
    )
    r = client.get(
        "/properties",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    assert (
        "invalid" in r.json()["detail"].lower()
        or "expired" in r.json()["detail"].lower()
    )
