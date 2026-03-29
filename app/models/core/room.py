"""Physical room unit."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_rooms_room_type_composite",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_rooms_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_type_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    housekeeping_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="clean",
    )
    housekeeping_priority: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="normal",
    )
