"""RBAC: forbidden actions for disallowed roles."""

from __future__ import annotations

from uuid import uuid4


def test_housekeeper_cannot_create_webhook_subscription(
    client,
    jwt_secret: str,
) -> None:
    import jwt

    tenant_id = uuid4()
    user_id = uuid4()
    token = jwt.encode(
        {"tenant_id": str(tenant_id), "sub": str(user_id), "role": "housekeeper"},
        jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "url": "https://example.com/hooks/openpms",
        "events": ["booking.created"],
        "is_active": True,
    }
    r = client.post("/webhooks/subscriptions", json=body, headers=headers)
    assert r.status_code == 403
