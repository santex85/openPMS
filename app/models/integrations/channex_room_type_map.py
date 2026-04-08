"""Maps OpenPMS room type to Channex room type for a property link."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ChannexRoomTypeMap(Base):
    __tablename__ = "channex_room_type_maps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            ["channex_property_links.tenant_id", "channex_property_links.id"],
            name="fk_channex_room_type_maps_property_link_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_channex_room_type_maps_room_type_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_room_type_maps_tenant_id_id",
        ),
        UniqueConstraint(
            "property_link_id",
            "room_type_id",
            name="uq_channex_room_type_maps_link_room_type",
        ),
        UniqueConstraint(
            "property_link_id",
            "channex_room_type_id",
            name="uq_channex_room_type_maps_link_channex_rt",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_link_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_type_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    channex_room_type_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channex_room_type_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
