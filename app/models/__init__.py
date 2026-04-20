"""Import model modules here so Alembic autogenerate discovers metadata."""

from app.models.auth import ApiKey, RefreshToken, User  # noqa: F401
from app.models.bookings import (  # noqa: F401
    Booking,
    BookingLine,
    FolioChargeCategory,
    FolioTransaction,
    Guest,
)
from app.models.core import (  # noqa: F401
    CountryPack,
    Property,
    Room,
    RoomHousekeepingEvent,
    RoomType,
    Tenant,
)
from app.models.integrations import (  # noqa: F401
    ChannexAriPushLog,
    ChannexBookingRevision,
    ChannexPropertyLink,
    ChannexRatePlanMap,
    ChannexRoomTypeMap,
    ChannexWebhookLog,
    CountryPackExtension,
    PropertyExtension,
    WebhookDeliveryLog,
    WebhookPendingDelivery,
    WebhookSubscription,
)
from app.models.rates import AvailabilityLedger, Rate, RatePlan  # noqa: F401
from app.models.audit.audit_log import AuditLog  # noqa: F401
from app.models.billing import (  # noqa: F401
    StripeCharge,
    StripeConnection,
    StripePaymentMethod,
    TaxConfig,
)
from app.models.notifications import EmailLog, EmailSettings  # noqa: F401
