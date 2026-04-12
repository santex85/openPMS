"""Per-property tax configuration (Phase 0)."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaxMode(str, enum.Enum):
    off = "off"
    inclusive = "inclusive"
    exclusive = "exclusive"


class TaxConfig(Base):
    __tablename__ = "tax_configs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tax_configs_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_tax_configs_property_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            name="uq_tax_configs_tenant_property",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_tax_configs_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tax_mode: Mapped[TaxMode] = mapped_column(
        PG_ENUM(TaxMode, name="tax_mode", create_type=False),
        nullable=False,
    )
    tax_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
