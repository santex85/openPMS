"""Log of outbound ARI pushes to Channex."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ChannexAriPushLog(Base):
    __tablename__ = "channex_ari_push_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            ["channex_property_links.tenant_id", "channex_property_links.id"],
            name="fk_channex_ari_push_logs_property_link_composite",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_ari_push_logs_tenant_id_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    property_link_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
