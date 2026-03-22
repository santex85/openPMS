"""Rate plan (BAR, corporate, etc.) for a property."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RatePlan(Base):
    __tablename__ = "rate_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_rate_plans_property_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_rate_plans_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cancellation_policy: Mapped[str] = mapped_column(Text, nullable=False)
