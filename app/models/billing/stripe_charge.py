"""Stripe charge / refund history (Phase 0)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StripeCharge(Base):
    __tablename__ = "stripe_charges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_charges_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_charges_property_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_stripe_charges_booking_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "folio_tx_id"],
            ["folio_transactions.tenant_id", "folio_transactions.id"],
            name="fk_stripe_charges_folio_tx_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_stripe_charges_tenant_id_id"),
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
    folio_tx_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    stripe_charge_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_pm_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
