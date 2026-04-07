"""Property (hotel / site) under a tenant."""

from datetime import time
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_properties_tenant_id_tenants",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_properties_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_pack_code: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey(
            "country_packs.code",
            name="fk_properties_country_pack_code_country_packs",
        ),
        nullable=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    checkin_time: Mapped[time] = mapped_column(Time, nullable=False)
    checkout_time: Mapped[time] = mapped_column(Time, nullable=False)
