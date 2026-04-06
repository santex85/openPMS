"""Add booking status check constraint.

Revision ID: p6q7r8s9t0u1
Revises: o4p5q6r7s8t9
Create Date: 2026-04-07

"""

from typing import Sequence, Union

from alembic import op

revision: str = "p6q7r8s9t0u1"
down_revision: Union[str, Sequence[str], None] = "o4p5q6r7s8t9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_booking_status",
        "bookings",
        "status IN ('pending','confirmed','checked_in','checked_out','cancelled','no_show')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_booking_status", "bookings", type_="check")
