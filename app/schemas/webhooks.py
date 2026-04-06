"""Webhook subscription API and documented outbound payload shapes (OpenAPI)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.webhook_events import VALID_WEBHOOK_EVENTS


class WebhookSubscriptionCreate(BaseModel):
    url: str = Field(
        ...,
        description="HTTPS endpoint that accepts POST with JSON body {event, data}.",
        examples=["https://integrations.example.com/hooks/openpms"],
    )
    events: list[str] = Field(
        ...,
        description="Event names to subscribe to.",
        examples=[["booking.created", "booking.updated"]],
    )
    is_active: bool = Field(
        True, description="Inactive subscriptions receive no deliveries."
    )

    model_config = ConfigDict(extra="forbid")


class WebhookDeliveryLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    webhook_subscription_id: UUID
    event_type: str
    attempt_number: int
    http_status_code: int | None
    error_message: str | None
    payload_json: dict[str, Any]
    created_at: datetime


class WebhookSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    url: str = Field(description="Target HTTPS URL.")
    events: list[str] = Field(
        description=f"Subscribed events; subset of: {sorted(VALID_WEBHOOK_EVENTS)}",
    )
    is_active: bool


class WebhookSubscriptionCreateResponse(WebhookSubscriptionRead):
    """`secret` is shown only once; store it to verify `X-Webhook-Signature` on receive."""

    secret: str = Field(
        ...,
        description="HMAC-SHA256 signing secret (prefix whsec_).",
        examples=["whsec_abc123..."],
    )


class WebhookSecretsReencryptRequest(BaseModel):
    """Fernet key produced by ``Fernet.generate_key().decode()`` (44-char URL-safe base64)."""

    new_fernet_key: str = Field(
        ...,
        description=(
            "Target Fernet key. After this call succeeds, set WEBHOOK_SECRET_FERNET_KEY "
            "to this exact value and restart the API."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class WebhookSecretsReencryptResponse(BaseModel):
    updated_count: int = Field(
        ...,
        ge=0,
        description="Number of subscriptions whose stored secret was re-encrypted.",
    )


class WebhookSubscriptionPatch(BaseModel):
    url: str | None = Field(
        None,
        description="New HTTPS URL.",
        examples=["https://integrations.example.com/hooks/v2"],
    )
    events: list[str] | None = Field(
        None,
        description="Replace subscribed events.",
    )
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")


# --- Reference payload shapes (for /docs examples; mirrors outbound `data` field) ---


class WebhookExampleBookingRead(BaseModel):
    """Payload `data` for event booking.created."""

    id: UUID = Field(description="Booking id.")
    tenant_id: UUID
    property_id: UUID
    guest_id: UUID
    rate_plan_id: UUID | None = None
    status: str
    source: str
    total_amount: str = Field(description="Decimal as string.")


class WebhookExampleBookingUpdated(BaseModel):
    """Payload `data` for booking.updated."""

    booking_id: UUID
    changed: dict[str, Any] = Field(
        description="Fields changed in this update.",
        examples=[{"status": "checked_in"}],
    )
    previous_values: dict[str, Any] = Field(
        description="Prior values for changed keys.",
        examples=[{"status": "confirmed"}],
    )


class WebhookExampleBookingCancelled(BaseModel):
    booking_id: UUID
    cancellation_reason: str


class WebhookExampleAvailability(BaseModel):
    room_type_id: UUID
    date: str = Field(description="ISO date (night).")
    available_rooms: int


class WebhookExampleRateUpdated(BaseModel):
    room_type_id: UUID
    rate_plan_id: UUID
    date: str
    price: str


class WebhookExampleGuestStay(BaseModel):
    booking_id: UUID
    guest_id: UUID
    room_id: str = Field(description="Room UUID string; may be empty if unassigned.")


class WebhookExampleGuestCheckedOut(WebhookExampleGuestStay):
    folio_balance: str = Field(
        description="Positive = guest owes; negative = overpay.",
        examples=["120.50"],
    )
