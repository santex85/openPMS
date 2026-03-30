"""Tenant isolation on additional read endpoints (RLS)."""

from __future__ import annotations


def test_tenant_b_sees_no_properties(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_b = tenant_isolation_booking_scenario["tenant_b"]
    r = client.get("/properties", headers=auth_headers(tenant_b))
    assert r.status_code == 200
    assert r.json() == []


def test_tenant_b_guest_list_empty(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
) -> None:
    tenant_b = tenant_isolation_booking_scenario["tenant_b"]
    r = client.get("/guests", headers=auth_headers(tenant_b))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_tenant_b_cannot_read_tenant_a_guest_detail(
    client,
    tenant_isolation_booking_scenario: dict,
    auth_headers,
    db_engine,
) -> None:
    """Guest id exists for tenant A; tenant B receives 404 via RLS (empty lookup)."""
    import asyncio
    from uuid import UUID

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.bookings.guest import Guest

    tenant_a: UUID = tenant_isolation_booking_scenario["tenant_a"]
    tenant_b: UUID = tenant_isolation_booking_scenario["tenant_b"]

    async def _gid() -> str:
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            async with session.begin():
                from sqlalchemy import text

                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"
                    ),
                    {"tid": str(tenant_a)},
                )
                gid = await session.scalar(
                    select(Guest.id).where(Guest.tenant_id == tenant_a).limit(1),
                )
        assert gid is not None
        return str(gid)

    guest_id = asyncio.run(_gid())
    r = client.get(f"/guests/{guest_id}", headers=auth_headers(tenant_b))
    assert r.status_code == 404
