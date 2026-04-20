"""MIG-18: extended PrenoAdapter mapping, merge, dedupe, statuses (fixtures only)."""

from __future__ import annotations

from pathlib import Path

from scripts.migrate.adapters.preno import PrenoAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _adapter(guests: str | None, bookings: str | None, **kwargs: bool) -> PrenoAdapter:
    return PrenoAdapter(
        guests_glob=guests,
        bookings_glob=bookings,
        include_cancelled=kwargs.get("include_cancelled", False),
    )


def test_country_iso_mapping() -> None:
    ad = _adapter(str(FIXTURES / "guests_edge.csv"), None)
    by_id = {g.external_id: g for g in ad.extract_guests()}
    assert by_id["edge-1"].nationality == "TH"
    assert by_id["edge-2"].nationality == "RU"
    assert by_id["edge-3"].nationality == "TH"
    assert by_id["edge-4"].nationality is None


def test_email_phone_normalization() -> None:
    ad = _adapter(str(FIXTURES / "guests_edge.csv"), None)
    by_id = {g.external_id: g for g in ad.extract_guests()}
    assert by_id["edge-1"].email == "mixed@example.com"
    assert by_id["edge-1"].phone == "+66812345678"
    assert by_id["edge-5"].email is None
    assert by_id["edge-5"].phone is None


def test_blacklisted_variants() -> None:
    ad = _adapter(str(FIXTURES / "guests_edge.csv"), None)
    by_id = {g.external_id: g for g in ad.extract_guests()}
    assert by_id["edge-2"].vip_status is True
    assert by_id["edge-6"].vip_status is True
    assert by_id["edge-7"].vip_status is True
    assert by_id["edge-8"].vip_status is True
    assert by_id["edge-9"].vip_status is False
    assert by_id["edge-10"].vip_status is False
    assert by_id["edge-5"].vip_status is False


def test_skip_rows_without_id() -> None:
    ad = _adapter(str(FIXTURES / "guests_edge.csv"), None)
    ids = {g.external_id for g in ad.extract_guests()}
    assert "NoId" not in ids
    assert "" not in ids


def test_multi_csv_merge_dedupe_guests() -> None:
    pattern = str(FIXTURES / "guests_multi_*.csv")
    ad = _adapter(pattern, None)
    gs = ad.extract_guests()
    by_id = {g.external_id: g for g in gs}
    assert len(gs) == 3
    assert by_id["dup-1"].email == "a_first@x.com"
    assert by_id["dup-1"].first_name == "First"


def test_multi_csv_merge_dedupe_bookings() -> None:
    pattern = str(FIXTURES / "bookings_multi_*.csv")
    ad = _adapter(None, pattern)
    bs = ad.extract_bookings()
    by_ext = {b.external_id: b for b in bs}
    assert len(bs) == 3
    assert by_ext["bm-1"].check_in.isoformat() == "2026-08-01"
    assert by_ext["bm-1"].guest.first_name == "X"


def test_booking_status_conversion() -> None:
    ad = PrenoAdapter(
        guests_glob=None,
        bookings_glob=str(FIXTURES / "bookings_statuses.csv"),
        include_cancelled=True,
    )
    by_ext = {b.external_id: b for b in ad.extract_bookings()}
    assert by_ext["bs-conf"].status == "confirmed"
    assert by_ext["bs-in"].status == "checked_in"
    assert by_ext["bs-out"].status == "checked_out"
    assert by_ext["bs-can"].status == "cancelled"
    assert by_ext["bs-noshow"].status == "no_show"
    assert by_ext["bs-empty"].status == "confirmed"


def test_include_cancelled_filter() -> None:
    path = str(FIXTURES / "bookings_statuses.csv")
    off = _adapter(None, path, include_cancelled=False)
    on = _adapter(None, path, include_cancelled=True)
    off_ids = {b.external_id for b in off.extract_bookings()}
    on_ids = {b.external_id for b in on.extract_bookings()}
    assert "bs-can" not in off_ids
    assert "bs-can" in on_ids
    assert "bs-conf" in off_ids and "bs-conf" in on_ids


def test_extract_room_types_rate_plans() -> None:
    pattern = str(FIXTURES / "bookings_multi_*.csv")
    ad = _adapter(None, pattern)
    rts = ad.extract_room_types()
    rps = ad.extract_rate_plans()
    names_rt = {r.name for r in rts}
    names_rp = {r.name for r in rps}
    assert names_rt == {"Deluxe", "Std", "Suite"}
    assert names_rp == {"BAR"}
