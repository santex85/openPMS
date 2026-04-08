"""Add Channex ARI restriction fields to rates.

Revision ID: t2u3v4w5x6y7
Revises: s0t1u2v3w4x5
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t2u3v4w5x6y7"
down_revision = "s0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rates",
        sa.Column(
            "stop_sell",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "rates",
        sa.Column("min_stay_arrival", sa.Integer(), nullable=True),
    )
    op.add_column(
        "rates",
        sa.Column("max_stay", sa.Integer(), nullable=True),
    )
    op.alter_column("rates", "stop_sell", server_default=None)


def downgrade() -> None:
    op.drop_column("rates", "max_stay")
    op.drop_column("rates", "min_stay_arrival")
    op.drop_column("rates", "stop_sell")
