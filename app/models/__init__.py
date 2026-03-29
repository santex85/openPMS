"""Import model modules here so Alembic autogenerate discovers metadata."""

from app.models.auth import RefreshToken, User  # noqa: F401
from app.models.bookings import (  # noqa: F401
    Booking,
    BookingLine,
    FolioTransaction,
    Guest,
)
from app.models.core import Property, Room, RoomType, Tenant  # noqa: F401
from app.models.rates import AvailabilityLedger, Rate, RatePlan  # noqa: F401
