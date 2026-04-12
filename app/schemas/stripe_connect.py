"""Stripe Connect OAuth API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StripeStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    status: Literal["not_connected", "connected"]
    livemode: bool | None = None
    connected_at: datetime | None = None


class StripeConnectUrlResponse(BaseModel):
    url: str = Field(..., description="Redirect the browser to this Stripe OAuth URL.")
