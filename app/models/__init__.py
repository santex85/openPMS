"""Import model modules here so Alembic autogenerate discovers metadata."""

from app.models.auth import ApiKey, RefreshToken, User  # noqa: F401
from app.models.bookings import (  # noqa: F401
    Booking,
    BookingLine,
    FolioTransaction,
    Guest,
)
from app.models.core import (  # noqa: F401
    Property,
    Room,
    RoomHousekeepingEvent,
    RoomType,
    Tenant,
)
from app.models.integrations import WebhookDeliveryLog, WebhookSubscription  # noqa: F401
from app.models.rates import AvailabilityLedger, Rate, RatePlan  # noqa: F401
from app.models.audit.audit_log import AuditLog  # noqa: F401
