"""Sanity tests for Preno CSV adapter."""

from __future__ import annotations

from pathlib import Path

from scripts.migrate.adapters.preno import PrenoAdapter


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_preno_validate_requires_glob(tmp_path: Path) -> None:
    ad = PrenoAdapter(guests_glob=None, bookings_glob=None)
    vr = ad.validate()
    assert vr.ok is False


def test_preno_guests_and_bookings_sample() -> None:
    guests = FIXTURES / "guests_sample.csv"
    bookings = FIXTURES / "bookings_sample.csv"
    ad = PrenoAdapter(
        guests_glob=str(guests),
        bookings_glob=str(bookings),
        include_cancelled=False,
    )
    vr = ad.validate()
    assert vr.ok is True
    gs = ad.extract_guests()
    assert len(gs) == 2
    assert gs[0].external_id == "g1"
    assert gs[0].email == "ann@example.com"
    bs = ad.extract_bookings()
    assert len(bs) == 1
    assert bs[0].external_id == "b1"
    assert bs[0].room_type_name == "Deluxe"
    assert bs[0].status == "confirmed"


def test_max_overlap_rooms() -> None:
    from datetime import date

    from scripts.migrate.models.records import BookingGuestSnapshot, BookingRecord

    g = BookingGuestSnapshot(
        first_name="A",
        last_name="B",
        email="a@b.com",
        phone="+10000000000",
    )
    b = [
        BookingRecord(
            external_id="1",
            check_in=date(2026, 1, 1),
            check_out=date(2026, 1, 5),
            room_type_name="Std",
            rate_plan_name="BAR",
            guest=g,
            status="confirmed",
        ),
        BookingRecord(
            external_id="2",
            check_in=date(2026, 1, 3),
            check_out=date(2026, 1, 7),
            room_type_name="Std",
            rate_plan_name="BAR",
            guest=g,
            status="confirmed",
        ),
        BookingRecord(
            external_id="3",
            check_in=date(2026, 1, 1),
            check_out=date(2026, 1, 2),
            room_type_name="Std",
            rate_plan_name="BAR",
            guest=g,
            status="cancelled",
        ),
    ]
    from scripts.migrate.core.pipeline import _max_parallel_rooms_by_type

    need = _max_parallel_rooms_by_type(b)
    assert need["Std"] == 2
