"""Composite indexes for bookings and folio_transactions list queries.

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Create Date: 2026-04-02

"""

from typing import Sequence, Union

from alembic import op

revision: str = "n3o4p5q6r7s8"
down_revision: Union[str, Sequence[str], None] = "m2n3o4p5q6r7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_bookings_tenant_property_id",
        "bookings",
        ["tenant_id", "property_id"],
        unique=False,
    )
    op.create_index(
        "ix_folio_transactions_tenant_booking_id",
        "folio_transactions",
        ["tenant_id", "booking_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_folio_transactions_tenant_booking_id",
        table_name="folio_transactions",
    )
    op.drop_index("ix_bookings_tenant_property_id", table_name="bookings")
