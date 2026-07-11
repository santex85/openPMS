"""Integration tests: property management reports (occupancy / revenue / KPI / CSV)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.auth.user import User
from app.models.bookings.booking import Booking
from app.models.bookings.booking_line import BookingLine
from app.models.bookings.folio_transaction import FolioTransaction
from app.models.bookings.guest import Guest
from app.models.core.property import Property
from app.models.core.room_type import RoomType
from app.models.core.tenant import Tenant
from app.models.rates.availability_ledger import AvailabilityLedger
from tests.db_seed import disable_row_security_for_test_seed


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


async def _seed_reports_fixture(db_engine: object) -> dict[str, object]:
    """
    Known numbers for 2028-06-01 .. 2028-06-02:

    Day 1: 1 active night @ 1000, ledger available=2 → occ 50%
    Day 2: 1 active night @ 1500 + cancelled night ignored, ledger available=2
    Folio on day1 local: spa Charge 200, tax 70, Payment 500, Payment refund -50
    Cancelled + no_show bookings excluded from room revenue / occupancy.
    """
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    owner_id = uuid4()
    d1 = date(2028, 6, 1)
    d2 = date(2028, 6, 2)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

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
                    name="ReportsTenant",
                    billing_email="reports@example.com",
                    status="active",
                ),
            )
            session.add(
                Tenant(
                    id=other_tenant_id,
                    name="OtherTenant",
                    billing_email="other@example.com",
                    status="active",
                ),
            )
            await session.flush()
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email="owner@reports.example.com",
                    password_hash=hash_password("Password123!"),
                    full_name="Reports Owner",
                    role="owner",
                    is_active=True,
                ),
            )
            prop = Property(
                tenant_id=tenant_id,
                name="Reports Inn",
                timezone="UTC",
                currency="THB",
                checkin_time=time(14, 0),
                checkout_time=time(11, 0),
            )
            session.add(prop)
            await session.flush()
            rt = RoomType(
                tenant_id=tenant_id,
                property_id=prop.id,
                name="Deluxe",
                base_occupancy=2,
                max_occupancy=2,
            )
            session.add(rt)
            await session.flush()
            for d, total, blocked in ((d1, 3, 1), (d2, 2, 0)):
                session.add(
                    AvailabilityLedger(
                        tenant_id=tenant_id,
                        room_type_id=rt.id,
                        date=d,
                        total_rooms=total,
                        booked_rooms=1,
                        blocked_rooms=blocked,
                    ),
                )
            guest = Guest(
                tenant_id=tenant_id,
                first_name="Ann",
                last_name="Active",
                email="ann@example.com",
                phone="+661111",
            )
            guest_c = Guest(
                tenant_id=tenant_id,
                first_name="Can",
                last_name="Cel",
                email="can@example.com",
                phone="+662222",
            )
            guest_ns = Guest(
                tenant_id=tenant_id,
                first_name="No",
                last_name="Show",
                email="ns@example.com",
                phone="+663333",
            )
            session.add_all([guest, guest_c, guest_ns])
            await session.flush()

            active = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest.id,
                status="confirmed",
                source="direct",
                total_amount=Decimal("2500.00"),
            )
            cancelled = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest_c.id,
                status="cancelled",
                source="direct",
                total_amount=Decimal("999.00"),
            )
            no_show = Booking(
                tenant_id=tenant_id,
                property_id=prop.id,
                guest_id=guest_ns.id,
                status="no_show",
                source="direct",
                total_amount=Decimal("888.00"),
            )
            session.add_all([active, cancelled, no_show])
            await session.flush()
            session.add_all(
                [
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        date=d1,
                        room_type_id=rt.id,
                        price_for_date=Decimal("1000.00"),
                    ),
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        date=d2,
                        room_type_id=rt.id,
                        price_for_date=Decimal("1500.00"),
                    ),
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=cancelled.id,
                        date=d1,
                        room_type_id=rt.id,
                        price_for_date=Decimal("999.00"),
                    ),
                    BookingLine(
                        tenant_id=tenant_id,
                        booking_id=no_show.id,
                        date=d2,
                        room_type_id=rt.id,
                        price_for_date=Decimal("888.00"),
                    ),
                ],
            )
            session.add_all(
                [
                    FolioTransaction(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        transaction_type="Charge",
                        amount=Decimal("200.00"),
                        category="spa",
                        description="Spa",
                        created_at=datetime(2028, 6, 1, 10, 0, tzinfo=timezone.utc),
                    ),
                    FolioTransaction(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        transaction_type="Charge",
                        amount=Decimal("70.00"),
                        category="tax",
                        description="VAT",
                        created_at=datetime(2028, 6, 1, 10, 5, tzinfo=timezone.utc),
                    ),
                    FolioTransaction(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        transaction_type="Charge",
                        amount=Decimal("1000.00"),
                        category="room_charge",
                        description="Room",
                        created_at=datetime(2028, 6, 1, 10, 10, tzinfo=timezone.utc),
                    ),
                    FolioTransaction(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        transaction_type="Payment",
                        amount=Decimal("500.00"),
                        category="card",
                        description="Card",
                        created_at=datetime(2028, 6, 1, 12, 0, tzinfo=timezone.utc),
                    ),
                    FolioTransaction(
                        tenant_id=tenant_id,
                        booking_id=active.id,
                        transaction_type="Payment",
                        amount=Decimal("-50.00"),
                        category="card",
                        description="Refund",
                        created_at=datetime(2028, 6, 1, 13, 0, tzinfo=timezone.utc),
                    ),
                ],
            )
            await session.flush()
            return {
                "tenant_id": tenant_id,
                "other_tenant_id": other_tenant_id,
                "owner_id": owner_id,
                "property_id": prop.id,
                "d1": d1,
                "d2": d2,
            }


@pytest.fixture
def reports_seed(db_engine: object) -> dict[str, object]:
    if not _database_url():
        pytest.skip("Set DATABASE_URL or TEST_DATABASE_URL for integration tests")
    return asyncio.run(_seed_reports_fixture(db_engine))


def test_occupancy_revenue_kpi_exact_numbers(
    client,
    auth_headers,
    reports_seed: dict[str, object],
) -> None:
    tid = reports_seed["tenant_id"]
    assert isinstance(tid, UUID)
    pid = reports_seed["property_id"]
    assert isinstance(pid, UUID)
    headers = auth_headers(tid, user_id=reports_seed["owner_id"], role="owner")
    q = "date_from=2028-06-01&date_to=2028-06-02"

    occ = client.get(f"/properties/{pid}/reports/occupancy?{q}", headers=headers)
    assert occ.status_code == 200, occ.text
    body = occ.json()
    assert body["currency"] == "THB"
    assert body["rows"][0] == {
        "date": "2028-06-01",
        "occupied_rooms": 1,
        "available_rooms": 2,
        "occupancy_pct": "50.00",
    }
    assert body["rows"][1]["occupied_rooms"] == 1
    assert body["rows"][1]["available_rooms"] == 2
    assert body["rows"][1]["occupancy_pct"] == "50.00"

    rev = client.get(f"/properties/{pid}/reports/revenue?{q}", headers=headers)
    assert rev.status_code == 200, rev.text
    rbody = rev.json()
    assert rbody["rows"][0]["room_revenue"] == "1000.00"
    assert rbody["rows"][0]["other_charges"] == {"spa": "200.00"}
    assert rbody["rows"][0]["tax_total"] == "70.00"
    assert rbody["rows"][0]["payments_total"] == "450.00"
    assert rbody["rows"][1]["room_revenue"] == "1500.00"
    assert rbody["room_revenue_total"] == "2500.00"
    assert rbody["tax_total"] == "70.00"
    assert rbody["payments_total"] == "450.00"
    assert rbody["other_charges_total"] == {"spa": "200.00"}

    kpi = client.get(f"/properties/{pid}/reports/kpi?{q}", headers=headers)
    assert kpi.status_code == 200, kpi.text
    k = kpi.json()
    assert k["sold_nights"] == 2
    assert k["available_nights"] == 4
    assert k["room_revenue"] == "2500.00"
    assert k["occupancy_pct"] == "50.00"
    assert k["adr"] == "1250.00"
    assert k["revpar"] == "625.00"


def test_reports_empty_period_and_single_day(
    client,
    auth_headers,
    reports_seed: dict[str, object],
) -> None:
    tid = reports_seed["tenant_id"]
    assert isinstance(tid, UUID)
    pid = reports_seed["property_id"]
    assert isinstance(pid, UUID)
    headers = auth_headers(tid, role="manager")

    empty = client.get(
        f"/properties/{pid}/reports/occupancy?date_from=2028-07-01&date_to=2028-07-01",
        headers=headers,
    )
    assert empty.status_code == 200
    assert empty.json()["rows"] == [
        {
            "date": "2028-07-01",
            "occupied_rooms": 0,
            "available_rooms": 0,
            "occupancy_pct": "0.00",
        },
    ]

    single = client.get(
        f"/properties/{pid}/reports/kpi?date_from=2028-06-01&date_to=2028-06-01",
        headers=headers,
    )
    assert single.status_code == 200
    k = single.json()
    assert k["sold_nights"] == 1
    assert k["available_nights"] == 2
    assert k["adr"] == "1000.00"
    assert k["revpar"] == "500.00"


def test_reports_rls_other_tenant_404(
    client,
    auth_headers,
    reports_seed: dict[str, object],
) -> None:
    other = reports_seed["other_tenant_id"]
    assert isinstance(other, UUID)
    pid = reports_seed["property_id"]
    assert isinstance(pid, UUID)
    headers = auth_headers(other, role="owner")
    r = client.get(
        f"/properties/{pid}/reports/occupancy?date_from=2028-06-01&date_to=2028-06-02",
        headers=headers,
    )
    assert r.status_code == 404


def test_reports_csv_bom_and_header(
    client,
    auth_headers,
    reports_seed: dict[str, object],
) -> None:
    tid = reports_seed["tenant_id"]
    assert isinstance(tid, UUID)
    pid = reports_seed["property_id"]
    assert isinstance(pid, UUID)
    headers = auth_headers(tid, role="owner")
    r = client.get(
        f"/properties/{pid}/reports/occupancy"
        f"?date_from=2028-06-01&date_to=2028-06-02&format=csv",
        headers=headers,
    )
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    raw = r.content
    assert raw.startswith(b"\xef\xbb\xbf") or raw.decode("utf-8-sig").startswith(
        "date,",
    )
    text_body = raw.decode("utf-8-sig")
    assert (
        text_body.splitlines()[0] == "date,occupied_rooms,available_rooms,occupancy_pct"
    )
    assert "2028-06-01,1,2,50.00" in text_body

    rev = client.get(
        f"/properties/{pid}/reports/revenue"
        f"?date_from=2028-06-01&date_to=2028-06-01&format=csv",
        headers=headers,
    )
    assert rev.status_code == 200
    assert rev.content.startswith(b"\xef\xbb\xbf") or rev.content.decode(
        "utf-8-sig",
    ).startswith("date,")

    kpi = client.get(
        f"/properties/{pid}/reports/kpi"
        f"?date_from=2028-06-01&date_to=2028-06-02&format=csv",
        headers=headers,
    )
    assert kpi.status_code == 200
    assert "adr" in kpi.content.decode("utf-8-sig").splitlines()[0]


def test_reports_range_validation(
    client,
    auth_headers,
    reports_seed: dict[str, object],
) -> None:
    tid = reports_seed["tenant_id"]
    assert isinstance(tid, UUID)
    pid = reports_seed["property_id"]
    assert isinstance(pid, UUID)
    headers = auth_headers(tid, role="owner")
    bad = client.get(
        f"/properties/{pid}/reports/occupancy?date_from=2028-06-02&date_to=2028-06-01",
        headers=headers,
    )
    assert bad.status_code == 422
