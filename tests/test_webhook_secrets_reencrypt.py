"""Fernet rotation: re-encrypt stored webhook subscription secrets via API."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.webhook_secrets import encrypt_plaintext_with_fernet_key
from app.models.integrations.webhook_subscription import WebhookSubscription


def test_encrypt_plaintext_with_fernet_key_roundtrip() -> None:
    key = Fernet.generate_key().decode()
    cipher = encrypt_plaintext_with_fernet_key("whsec_example", key)
    assert Fernet(key.encode()).decrypt(cipher.encode()).decode() == "whsec_example"


def test_encrypt_plaintext_with_fernet_key_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid Fernet"):
        encrypt_plaintext_with_fernet_key("x", "not-a-valid-fernet-key-material")


def test_webhook_secrets_reencrypt_integration(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
    db_engine,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    new_key = Fernet.generate_key().decode()

    r1 = client.post(
        "/webhooks/subscriptions",
        headers=h,
        json={
            "url": "https://example.com/openpms-webhook-reenc",
            "events": ["booking.created"],
            "is_active": True,
        },
    )
    assert r1.status_code == 201, r1.text
    sub_id = UUID(r1.json()["id"])
    plain_secret = r1.json()["secret"]

    r2 = client.post(
        "/webhooks/subscriptions/reencrypt-secrets",
        headers=h,
        json={"new_fernet_key": new_key},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["updated_count"] == 1

    async def _read_secret() -> str:
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tid)},
            )
            row = await session.scalar(
                select(WebhookSubscription).where(WebhookSubscription.id == sub_id),
            )
            assert row is not None
            return row.secret

    stored = asyncio.run(_read_secret())
    assert Fernet(new_key.encode()).decrypt(stored.encode()).decode() == plain_secret


def test_webhook_secrets_reencrypt_manager_forbidden(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="manager")
    r = client.post(
        "/webhooks/subscriptions/reencrypt-secrets",
        headers=h,
        json={"new_fernet_key": Fernet.generate_key().decode()},
    )
    assert r.status_code == 403


def test_webhook_secrets_reencrypt_invalid_key(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/webhooks/subscriptions/reencrypt-secrets",
        headers=h,
        json={"new_fernet_key": "!!!"},
    )
    assert r.status_code == 422
