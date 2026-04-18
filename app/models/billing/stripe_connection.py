"""Stripe Connect OAuth link per property (Phase 0)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StripeConnection(Base):
    __tablename__ = "stripe_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_connections_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_connections_property_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            name="uq_stripe_connections_tenant_property",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_stripe_connections_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    stripe_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
