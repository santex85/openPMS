"""Country pack preset (builtin or tenant-defined)."""

from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class CountryPack(Base):
    __tablename__ = "country_packs"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_country_packs_tenant_id_tenants"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    currency_symbol: Mapped[str] = mapped_column(String(8), nullable=False)
    currency_symbol_position: Mapped[str] = mapped_column(String(6), nullable=False)
    currency_decimal_places: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    date_format: Mapped[str] = mapped_column(String(20), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    default_checkin_time: Mapped[time] = mapped_column(Time, nullable=False)
    default_checkout_time: Mapped[time] = mapped_column(Time, nullable=False)
    taxes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    payment_methods: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    fiscal_year_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __mapper_args__: dict[str, object] = {"eager_defaults": True}
