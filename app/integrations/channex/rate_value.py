"""Format nightly prices for Channex ARI (restrictions) API."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# ISO 4217 minor units (digits after decimal). Omitted codes default to 2.
_MINOR_UNITS: dict[str, int] = {
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
    "CLF": 4,
    "UYW": 4,
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
}

_DEFAULT_MINOR_DIGITS = 2


def currency_exponent(currency_code: str) -> int:
    """Return ISO 4217 decimal exponent for the currency (0 = whole units only)."""
    c = (currency_code or "").strip().upper()[:3]
    if len(c) != 3:
        return _DEFAULT_MINOR_DIGITS
    return _MINOR_UNITS.get(c, _DEFAULT_MINOR_DIGITS)


def channex_rate_string(price: Decimal, currency_code: str) -> str:
    """
    Channex accepts rate as a decimal string (e.g. \"200.00\") or minor-unit int.
    Using a quantized string avoids float drift and matches API examples.
    """
    exp = currency_exponent(currency_code)
    step = Decimal(10) ** -exp
    q = price.quantize(step, rounding=ROUND_HALF_UP)
    return format(q, "f")
