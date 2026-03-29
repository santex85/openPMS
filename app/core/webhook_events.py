"""Webhook event name constants."""

BOOKING_CREATED = "booking.created"
BOOKING_UPDATED = "booking.updated"
BOOKING_CANCELLED = "booking.cancelled"
AVAILABILITY_CHANGED = "availability.changed"
RATE_UPDATED = "rate.updated"
GUEST_CHECKED_IN = "guest.checked_in"
GUEST_CHECKED_OUT = "guest.checked_out"

VALID_WEBHOOK_EVENTS: frozenset[str] = frozenset(
    {
        BOOKING_CREATED,
        BOOKING_UPDATED,
        BOOKING_CANCELLED,
        AVAILABILITY_CHANGED,
        RATE_UPDATED,
        GUEST_CHECKED_IN,
        GUEST_CHECKED_OUT,
    },
)
