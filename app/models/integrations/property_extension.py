"""Property-level activation and config for a country-pack extension."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class PropertyExtension(Base):
    __tablename__ = "property_extensions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_property_extensions_tenant_id_tenants",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_property_extensions_property_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extension_id"],
            [
                "country_pack_extensions.tenant_id",
                "country_pack_extensions.id",
            ],
            name="fk_property_extensions_extension_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "extension_id",
            name="uq_property_extensions_prop_ext",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_property_extensions_tenant_id_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    extension_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )

    __mapper_args__: dict[str, object] = {"eager_defaults": True}
