"""API schemas for email audit rows (TZ-16)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmailLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID | None
    booking_id: UUID | None
    to_address: str
    template_name: str
    subject: str
    status: str
    resend_id: str | None
    error_message: str | None
    sent_at: datetime
