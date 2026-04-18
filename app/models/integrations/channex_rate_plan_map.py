"""Maps OpenPMS rate plan to Channex rate plan under a room type map."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ChannexRatePlanMap(Base):
    __tablename__ = "channex_rate_plan_maps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "room_type_map_id"],
            [
                "channex_room_type_maps.tenant_id",
                "channex_room_type_maps.id",
            ],
            name="fk_channex_rate_plan_maps_room_type_map_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_plan_id"],
            ["rate_plans.tenant_id", "rate_plans.id"],
            name="fk_channex_rate_plan_maps_rate_plan_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_rate_plan_maps_tenant_id_id",
        ),
        UniqueConstraint(
            "room_type_map_id",
            "rate_plan_id",
            name="uq_channex_rate_plan_maps_map_rate",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_type_map_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    rate_plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    channex_rate_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channex_rate_plan_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
