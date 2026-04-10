"""Add external_booking_id to bookings for Channex OTA correlation.

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("external_booking_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_bookings_tenant_external_booking_id",
        "bookings",
        ["tenant_id", "external_booking_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bookings_tenant_external_booking_id", table_name="bookings")
    op.drop_column("bookings", "external_booking_id")
