"""Booking lifecycle status transitions (strict FSM).

Linear: pending → confirmed → checked_in → checked_out.
Branches: pending|confirmed → cancelled; confirmed → no_show.
Terminal: cancelled, no_show, checked_out — only idempotent same-status updates.

checked_in → cancelled is intentionally forbidden (not in spec).
"""

from __future__ import annotations

# All statuses we persist in lowercase for comparisons.
KNOWN_STATUSES = frozenset(
    {"pending", "confirmed", "checked_in", "checked_out", "cancelled", "no_show"},
)
TERMINAL_STATUSES = frozenset({"cancelled", "no_show", "checked_out"})


class BookingStatusTransitionError(Exception):
    """Invalid booking status change."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_booking_status(status: str) -> str:
    return status.strip().lower()


def validate_status_transition(old_status: str, new_status: str) -> None:
    """
    Raise BookingStatusTransitionError if old → new is not allowed.
    Idempotent: same normalized status is always OK.
    """
    o = normalize_booking_status(old_status)
    n = normalize_booking_status(new_status)
    if o == n:
        return
    if n not in KNOWN_STATUSES:
        raise BookingStatusTransitionError(
            f"unknown target status {new_status!r}; expected one of {sorted(KNOWN_STATUSES)}",
        )
    if o not in KNOWN_STATUSES:
        raise BookingStatusTransitionError(
            "cannot change status: booking has an unrecognized current status",
        )
    if o in TERMINAL_STATUSES:
        raise BookingStatusTransitionError(
            f"cannot transition from terminal status {o!r} to {n!r}",
        )

    allowed: dict[str, frozenset[str]] = {
        "pending": frozenset({"confirmed", "cancelled"}),
        "confirmed": frozenset({"checked_in", "cancelled", "no_show"}),
        "checked_in": frozenset({"checked_out"}),
    }
    next_ok = allowed.get(o)
    if next_ok is None or n not in next_ok:
        raise BookingStatusTransitionError(
            f"cannot transition booking status from {o!r} to {n!r}",
        )
