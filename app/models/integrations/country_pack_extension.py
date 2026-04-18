"""Registered country-pack extension (integrator webhook metadata)."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class CountryPackExtension(Base):
    __tablename__ = "country_pack_extensions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_country_pack_extensions_tenant_id_tenants",
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_country_pack_extensions_tenant_code"
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_country_pack_extensions_tenant_id_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    required_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    ui_config_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
