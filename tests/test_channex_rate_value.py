"""Channex ARI rate string formatting (currency exponent)."""

from __future__ import annotations

from decimal import Decimal

from app.integrations.channex.rate_value import channex_rate_string, currency_exponent


def test_usd_two_decimals() -> None:
    assert currency_exponent("usd") == 2
    assert channex_rate_string(Decimal("199.5"), "USD") == "199.50"
    assert channex_rate_string(Decimal("2500"), "THB") == "2500.00"


def test_jpy_zero_decimals() -> None:
    assert currency_exponent("JPY") == 0
    assert channex_rate_string(Decimal("199.5"), "JPY") == "200"
