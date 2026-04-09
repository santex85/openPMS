"""GET /rates/batch — multi room type calendar load."""

from __future__ import annotations

import asyncio
import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.rate import Rate
from app.models.rates.rate_plan import RatePlan


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def auth_headers():
    import jwt
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    secret = os.environ["JWT_SECRET"]

    def _make(tenant_id, user_id, *, role: str = "owner"):
        token = jwt.encode(
            {
                "tenant_id": str(tenant_id),
                "sub": str(user_id),
                "role": role,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


def test_get_rates_batch_matches_single_room_get(
    client: object,
    auth_headers: object,
    db_engine: object,
) -> None:
    from starlette.testclient import TestClient

    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    assert isinstance(client, TestClient)

    make_headers = auth_headers  # type: ignore[assignment]

    async def _seed() -> dict[str, object]:
        tenant_id = uuid4()
        owner_id = uuid4()
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
                    {"tid": str(tenant_id)},
                )
                session.add(
                    Tenant(
                        id=tenant_id,
                        name="BatchRatesTenant",
                        billing_email="batch@example.com",
                        status="active",
                    ),
                )
                session.add(
                    User(
                        id=owner_id,
                        tenant_id=tenant_id,
                        email="o@batch.example.com",
                        password_hash=hash_password("secret"),
                        full_name="Owner",
                        role="owner",
                    ),
                )
                prop = Property(
                    tenant_id=tenant_id,
                    name="Batch Hotel",
                    timezone="UTC",
                    currency="USD",
                    checkin_time=time(14, 0),
                    checkout_time=time(11, 0),
                )
                session.add(prop)
                await session.flush()
                rt1 = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="A",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                rt2 = RoomType(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="B",
                    base_occupancy=2,
                    max_occupancy=2,
                )
                session.add_all([rt1, rt2])
                await session.flush()
                rp = RatePlan(
                    tenant_id=tenant_id,
                    property_id=prop.id,
                    name="BAR",
                    cancellation_policy="none",
                )
                session.add(rp)
                await session.flush()
                session.add_all(
                    [
                        Rate(
                            tenant_id=tenant_id,
                            room_type_id=rt1.id,
                            rate_plan_id=rp.id,
                            date=date(2026, 6, 1),
                            price=Decimal("10.00"),
                        ),
                        Rate(
                            tenant_id=tenant_id,
                            room_type_id=rt2.id,
                            rate_plan_id=rp.id,
                            date=date(2026, 6, 2),
                            price=Decimal("20.00"),
                        ),
                    ],
                )
        return {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "rt1": rt1.id,
            "rt2": rt2.id,
            "rp": rp.id,
        }

    ids = asyncio.run(_seed())
    tid = ids["tenant_id"]
    oid = ids["owner_id"]
    headers = make_headers(tid, oid)  # type: ignore[operator]

    r_batch = client.get(
        "/rates/batch",
        params={
            "rate_plan_id": str(ids["rp"]),
            "start_date": "2026-06-01",
            "end_date": "2026-06-07",
            "room_type_ids": f"{ids['rt1']},{ids['rt2']}",
        },
        headers=headers,
    )
    assert r_batch.status_code == 200, r_batch.text
    batch_rows = r_batch.json()
    assert len(batch_rows) == 2

    g1 = client.get(
        "/rates",
        params={
            "room_type_id": str(ids["rt1"]),
            "rate_plan_id": str(ids["rp"]),
            "start_date": "2026-06-01",
            "end_date": "2026-06-07",
        },
        headers=headers,
    )
    assert g1.status_code == 200, g1.text
    g2 = client.get(
        "/rates",
        params={
            "room_type_id": str(ids["rt2"]),
            "rate_plan_id": str(ids["rp"]),
            "start_date": "2026-06-01",
            "end_date": "2026-06-07",
        },
        headers=headers,
    )
    assert g2.status_code == 200, g2.text
    combined = g1.json() + g2.json()
    assert sorted(batch_rows, key=lambda x: (x["room_type_id"], x["date"])) == sorted(
        combined,
        key=lambda x: (x["room_type_id"], x["date"]),
    )


def test_get_rates_batch_empty_room_type_ids_422(
    client: object,
    auth_headers: object,
    smoke_scenario: dict[str, UUID],
) -> None:
    from starlette.testclient import TestClient

    if not _database_url():
        pytest.skip("Set DATABASE_URL for integration tests")
    assert isinstance(client, TestClient)
    make_headers = auth_headers  # type: ignore[assignment]
    headers = make_headers(
        smoke_scenario["tenant_id"],
        smoke_scenario["owner_id"],
    )  # type: ignore[operator]
    res = client.get(
        "/rates/batch",
        params={
            "rate_plan_id": str(smoke_scenario["rate_plan_id"]),
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
            "room_type_ids": "  ,  ",
        },
        headers=headers,
    )
    assert res.status_code == 422
