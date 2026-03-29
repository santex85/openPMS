"""Audit log for housekeeping status changes on a physical room."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import func

from app.db.base import Base


class RoomHousekeepingEvent(Base):
    __tablename__ = "room_housekeeping_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "room_id"],
            ["rooms.tenant_id", "rooms.id"],
            name="fk_hk_events_room_composite",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "changed_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_hk_events_user_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_room_housekeeping_events_tenant_id_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    old_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
