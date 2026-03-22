"""Stay night arithmetic: [check_in, check_out) half-open interval."""

from datetime import date, timedelta

MAX_STAY_NIGHTS = 365


def iter_stay_nights(check_in: date, check_out: date) -> list[date]:
    """Return each night date covered by the stay (check-out night excluded)."""
    if check_out <= check_in:
        msg = "check_out must be after check_in"
        raise ValueError(msg)
    nights: list[date] = []
    d = check_in
    while d < check_out:
        nights.append(d)
        d += timedelta(days=1)
    if len(nights) > MAX_STAY_NIGHTS:
        msg = f"stay cannot exceed {MAX_STAY_NIGHTS} nights"
        raise ValueError(msg)
    return nights
