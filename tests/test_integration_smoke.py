"""Integration smoke tests: guests, housekeeping, api-keys, webhooks, audit read."""

from __future__ import annotations

import asyncio
import os
from datetime import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room import Room
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_smoke_tenant() -> dict[str, UUID]:
    tenant_id = uuid4()
    owner_id = uuid4()
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                {"tid": str(tenant_id)},
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="SmokeTenant",
                    billing_email="smoke@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="owner@smoke.example.com",
                    password_hash=hash_password("secret"),
                    full_name="Owner",
                    role="owner",
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="Smoke Property",
                timezone="UTC",
                currency="USD",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            room_type = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Standard",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(room_type)
            await session.flush()
            room = Room(
                tenant_id=tenant_id,
                room_type_id=room_type.id,
                name="101",
                status="available",
                housekeeping_status="dirty",
                housekeeping_priority="normal",
            )
            session.add(room)
            await session.flush()
            property_id = prop.id
            room_id = room.id

    await engine.dispose()
    return {
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "property_id": property_id,
        "room_id": room_id,
    }


@pytest.fixture
def smoke_scenario() -> dict[str, UUID]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")
    return asyncio.run(_seed_smoke_tenant())


def test_guests_search_and_create(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.get("/guests", headers=h, params={"q": "nobody"})
    assert r.status_code == 200
    assert r.json() == []
    cr = client.post(
        "/guests",
        headers=h,
        json={
            "first_name": "Sam",
            "last_name": "Smoke",
            "email": "sam.smoke@example.com",
            "phone": "+15550000001",
        },
    )
    assert cr.status_code == 201
    body = cr.json()
    assert body["email"] == "sam.smoke@example.com"
    assert "id" in body


def test_housekeeping_list_and_patch(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    prop_id = str(smoke_scenario["property_id"])
    room_id = smoke_scenario["room_id"]
    h = auth_headers_user(tid, oid, role="owner")
    lr = client.get(
        "/housekeeping",
        headers=h,
        params={"property_id": prop_id},
    )
    assert lr.status_code == 200
    rooms = lr.json()
    assert len(rooms) >= 1
    assert any(str(r["id"]) == str(room_id) for r in rooms)
    pr = client.patch(
        f"/housekeeping/{room_id}",
        headers=h,
        json={"housekeeping_status": "clean"},
    )
    assert pr.status_code == 200
    assert pr.json()["housekeeping_status"] == "clean"


def test_properties_patch(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    pid = smoke_scenario["property_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.patch(
        f"/properties/{pid}",
        headers=h,
        json={"name": "Updated Smoke Property"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Smoke Property"


def test_api_key_create_returns_plaintext_once(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    r = client.post(
        "/api-keys",
        headers=h,
        json={
            "name": "integration-test",
            "scopes": ["guests:read"],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "integration-test"
    assert "key" in data
    assert len(data["key"]) > 20


def test_webhook_subscription_create(
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
            "url": "https://hooks.example.com/openpms",
            "events": ["booking.created"],
            "is_active": True,
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["url"] == "https://hooks.example.com/openpms"
    assert "secret" in data
    assert "booking.created" in data["events"]


def test_audit_log_lists_after_mutation(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers_user,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    h = auth_headers_user(tid, oid, role="owner")
    client.post(
        "/guests",
        headers=h,
        json={
            "first_name": "Audit",
            "last_name": "Trail",
            "email": "audit.trail@example.com",
            "phone": "+15550000002",
        },
    )
    ar = client.get("/audit-log", headers=h, params={"limit": 20})
    assert ar.status_code == 200
    rows = ar.json()
    assert any(e.get("action") == "guest.create" for e in rows)
