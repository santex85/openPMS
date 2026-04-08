"""Nightly (or per-date) price for a room type under a rate plan."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Rate(Base):
    __tablename__ = "rates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_rates_room_type_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_plan_id"],
            ["rate_plans.tenant_id", "rate_plans.id"],
            name="fk_rates_rate_plan_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "room_type_id",
            "rate_plan_id",
            "date",
            name="uq_rates_tenant_room_type_plan_date",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_type_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    rate_plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    stop_sell: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_stay_arrival: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_stay: Mapped[int | None] = mapped_column(Integer, nullable=True)
