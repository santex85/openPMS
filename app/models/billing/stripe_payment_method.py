"""Saved Stripe payment methods (Phase 0)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StripePaymentMethod(Base):
    __tablename__ = "stripe_payment_methods"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_payment_methods_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_payment_methods_property_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_stripe_payment_methods_booking_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_stripe_payment_methods_tenant_id_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    booking_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    stripe_pm_id: Mapped[str] = mapped_column(String(255), nullable=False)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    card_brand: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_exp_month: Mapped[int | None] = mapped_column(SmallInteger(), nullable=True)
    card_exp_year: Mapped[int | None] = mapped_column(SmallInteger(), nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
