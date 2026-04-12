"""Unit tests for stay_dates helpers."""

from datetime import date, timedelta

import pytest

from app.services.stay_dates import MAX_STAY_NIGHTS, iter_stay_nights


def test_iter_stay_nights_half_open_interval() -> None:
    nights = iter_stay_nights(date(2026, 3, 1), date(2026, 3, 4))
    assert nights == [date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)]


def test_iter_stay_nights_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="check_out"):
        iter_stay_nights(date(2026, 3, 5), date(2026, 3, 5))


def test_iter_stay_nights_rejects_too_long_stay() -> None:
    ci = date(2026, 1, 1)
    co = ci + timedelta(days=MAX_STAY_NIGHTS + 1)
    with pytest.raises(ValueError, match="cannot exceed"):
        iter_stay_nights(ci, co)
