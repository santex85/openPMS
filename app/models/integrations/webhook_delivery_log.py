"""Logged webhook HTTP delivery attempt."""

from datetime import datetime
from uuid import UUID, uuid4

from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import func

from app.db.base import Base


class WebhookDeliveryLog(Base):
    __tablename__ = "webhook_delivery_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_webhook_delivery_logs_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "webhook_subscription_id"],
            ["webhook_subscriptions.tenant_id", "webhook_subscriptions.id"],
            name="fk_webhook_delivery_logs_subscription_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_webhook_delivery_logs_tenant_id_id"),
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
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
