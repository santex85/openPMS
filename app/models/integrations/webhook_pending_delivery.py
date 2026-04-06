"""Queued outbound webhook delivery (async worker; survives HTTP retry delays)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookPendingDelivery(Base):
    __tablename__ = "webhook_pending_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_webhook_pending_deliveries_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "webhook_subscription_id"],
            ["webhook_subscriptions.tenant_id", "webhook_subscriptions.id"],
            name="fk_webhook_pending_deliveries_subscription_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_webhook_pending_deliveries_tenant_id_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    webhook_subscription_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
