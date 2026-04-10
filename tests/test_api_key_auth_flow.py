"""X-API-Key authentication: scoped access via require_scopes."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.api_scopes import PROPERTIES_READ
from app.models.auth.api_key import ApiKey
from app.models.core.property import Property
from app.models.core.tenant import Tenant
from app.services.api_key_service import hash_api_key

from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_tenant_with_properties_scope(*, plaintext: str) -> tuple[str, str]:
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL required")
    tenant_id = uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    digest = hash_api_key(plaintext)
    async with factory() as session:
        async with session.begin():
            await disable_row_security_for_test_seed(session)
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="ApiKeyTenant",
                    billing_email="ak@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                Property(
                    tenant_id=tenant_id,
                    name="AK Prop",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                ),
            )
            await session.flush()
            session.add(
                ApiKey(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    key_hash=digest,
                    name="test-properties-only",
                    scopes=[PROPERTIES_READ],
                    is_active=True,
                    expires_at=None,
                ),
            )
    await engine.dispose()
    return str(tenant_id), plaintext


@pytest.fixture
def properties_only_api_key() -> tuple[str, str]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    plain = f"opms_pytest_{uuid4().hex}"
    return asyncio.run(_seed_tenant_with_properties_scope(plaintext=plain))


def test_api_key_allows_scoped_route(
    client,
    properties_only_api_key: tuple[str, str],
) -> None:
    _tid, plain = properties_only_api_key
    r = client.get("/properties", headers={"X-API-Key": plain})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_key_forbidden_without_scope(
    client,
    properties_only_api_key: tuple[str, str],
) -> None:
    """Key has only properties:read; guests list requires guests:read."""
    _tid, plain = properties_only_api_key
    r = client.get("/guests", headers={"X-API-Key": plain})
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()


def test_api_key_unknown_returns_401(client) -> None:
    r = client.get(
        "/properties",
        headers={"X-API-Key": "opms_invalid_key_that_does_not_exist"},
    )
    assert r.status_code == 401
